"""The Gemini Live model connection, driven by the Core through ``synth_sdk.live``.

One :class:`GeminiLiveEngine` holds every session the Core opens. For each it keeps a
WebSocket to the model and two tasks: one receives what the model does and reports it, the
other sends the caller's audio (with manual speech detection when asked). The connection and
the SDK's request objects come from two small injected objects (``connect`` and ``wire``) so
the engine is tested with a fake library.

Reconnection is this extension's business, not the Core's: when the server says go-away, the
session reaches its time limit or the socket drops, the engine reconnects with Google's
resumption handle (the context is kept server-side), or, without one, starts over with the
last exchanges as context. The Core is told ``reconnecting`` and ``connected`` and the call
goes on.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import convert

from synth_sdk.live import LiveEngine, LiveSession

#: Google closes a Live socket after about ten minutes; reconnect a minute before.
RECONNECT_AFTER_SECONDS = 9 * 60
FLUSH_INTERVAL = 0.2
RECONNECT_RETRIES = 3
RECONNECT_DELAY = 2.0
#: Lines of the conversation kept to restart a session that cannot be resumed.
LOG_LINES = 40


class Wire(Protocol):
    """The SDK's request objects."""

    def activity_start(self) -> Any: ...

    def activity_end(self) -> Any: ...

    def blob(self, data: bytes, mime_type: str) -> Any: ...

    def function_response(self, call_id: str, name: str, response: dict[str, Any]) -> Any: ...


#: ``(model, config) -> async context manager`` yielding the live session.
Connect = Callable[[str, dict[str, Any]], Any]
Load = Callable[[], Awaitable[convert.Settings]]


@dataclass
class _Run:
    session: LiveSession
    settings: convert.Settings
    cm: Any = None
    conn: Any = None
    started: float = 0.0
    handle: str | None = None
    generating: bool = False
    user_speaking: bool = False
    go_away: bool = False
    reconnecting: bool = False
    closing: bool = False
    buffer: bytearray = field(default_factory=bytearray)
    pending: list[str] = field(default_factory=list)
    log: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class GeminiLiveEngine(LiveEngine):
    label = "Gemini Live"
    input_rate = 16000
    output_rate = convert.OUTPUT_RATE
    #: Reconnection is handled here, so the Core arms no rejoin timer.
    max_session_seconds = 0

    def __init__(
        self,
        load_settings: Load,
        connect: Callable[[convert.Settings], Connect],
        wire: Wire,
        log: Any,
        *,
        retry_delay: float = RECONNECT_DELAY,
        flush_interval: float = FLUSH_INTERVAL,
        reconnect_after: float = RECONNECT_AFTER_SECONDS,
    ) -> None:
        self._load = load_settings
        self._connector = connect
        self._wire = wire
        self._log = log
        self._retry_delay = retry_delay
        self._flush_interval = flush_interval
        self._reconnect_after = reconnect_after
        self._runs: dict[str, _Run] = {}

    # ------------------------------------------------------------------ Core side

    async def open(self, session: LiveSession) -> None:
        settings = await self._load()
        if not settings.api_key:
            raise RuntimeError("no Gemini API key is set")
        run = _Run(session=session, settings=settings, handle=session.resume_handle)
        await self._connect(run)
        self._runs[session.session_id] = run
        run.tasks = [
            asyncio.create_task(self._receive(run), name=f"gemini_rx_{session.session_id}"),
            asyncio.create_task(self._send(run), name=f"gemini_tx_{session.session_id}"),
        ]
        if not run.handle:
            await self._kick(run, convert.kick_text())
        await session.state("connected", run.handle or "")

    async def audio(self, session_id: str, pcm: bytes, rate: int) -> None:
        run = self._runs.get(session_id)
        if run is not None and not run.closing and not run.reconnecting:
            run.buffer.extend(pcm)

    async def text(self, session_id: str, text: str, kind: str) -> None:
        run = self._runs.get(session_id)
        if run is None or run.closing or not text.strip():
            return
        if kind == "turn":
            await self._say(run, text)
        elif run.generating or run.reconnecting:
            # Text injected while the model speaks would interrupt or garble the reply.
            run.pending.append(text)
        else:
            await self._say(run, f"[System Context Update] {text}")

    async def tool_result(
        self, session_id: str, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        run = self._runs.get(session_id)
        if run is None or run.closing or run.conn is None:
            return
        response = {k: v for k, v in result.items() if k != "scheduling"}
        try:
            await run.conn.send_tool_response(
                function_responses=[self._wire.function_response(call_id, name, response)]
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(f"tool response for {name!r} was not sent: {exc}")

    async def close(self, session_id: str) -> None:
        run = self._runs.pop(session_id, None)
        if run is None:
            return
        run.closing = True
        current = asyncio.current_task()
        for task in run.tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(*run.tasks, return_exceptions=True)
        await self._disconnect(run)

    # ----------------------------------------------------------------- connection

    async def _connect(self, run: _Run) -> None:
        settings = run.settings
        config = convert.build_config(
            settings=settings,
            system_instruction=run.session.system_instruction,
            tools=run.session.tools,
            voice=run.session.voice,
            resume_handle=run.handle,
        )
        run.cm = self._connector(settings)(convert.choose_model(settings), config)
        try:
            run.conn = await run.cm.__aenter__()
        except Exception:
            run.cm = None
            raise
        run.started = time.monotonic()
        run.go_away = False
        run.user_speaking = False
        run.generating = False

    async def _disconnect(self, run: _Run) -> None:
        cm, run.cm, run.conn = run.cm, None, None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - a socket already dead
                self._log.debug(f"closing the model connection: {exc}")

    async def _reconnect(self, run: _Run) -> bool:
        """Open a new socket for the same call. ``False`` when it could not be done."""
        run.reconnecting = True
        resumed = bool(run.handle)
        await run.session.state("reconnecting", run.handle or "")
        await self._disconnect(run)
        run.buffer.clear()
        error = ""
        for attempt in range(1, RECONNECT_RETRIES + 1):
            if run.closing:
                return False
            try:
                await self._connect(run)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:200]
                self._log.warning(f"reconnect {attempt}/{RECONNECT_RETRIES}: {error}")
                if attempt < RECONNECT_RETRIES:
                    await asyncio.sleep(self._retry_delay * attempt)
                continue
            if not resumed:
                await self._kick(
                    run,
                    convert.kick_text(
                        reconnect=True, history=convert.history_snippet(list(run.log))
                    ),
                )
            run.reconnecting = False
            await run.session.state("connected", run.handle or "")
            return True
        run.reconnecting = False
        await run.session.error(f"the model connection could not be restored: {error}")
        await run.session.state("closed")
        return False

    async def _say(self, run: _Run, text: str) -> None:
        if run.conn is None:
            return
        try:
            await run.conn.send_realtime_input(text=text)
        except Exception as exc:  # noqa: BLE001
            self._log.debug(f"text was not sent: {exc}")

    async def _kick(self, run: _Run, text: str) -> None:
        await self._say(run, text)

    # -------------------------------------------------------------------- receive

    async def _receive(self, run: _Run) -> None:
        """Receive until the session is closed or cannot be restored."""
        while not run.closing:
            try:
                if not await self._turns(run):
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if run.closing:
                    return
                self._log.warning(f"the model connection failed: {exc}")
                await run.session.error(str(exc))
                if not await self._reconnect(run):
                    return

    async def _turns(self, run: _Run) -> bool:
        """Report turns until something needs a new connection. ``False`` when the
        session is over (closed, or a reconnect failed)."""
        while not run.closing and run.conn is not None:
            user: list[str] = []
            model: list[str] = []
            interrupted = False
            messages = 0
            # ``receive()`` covers one model turn and then ends: loop for the next.
            async for message in run.conn.receive():
                if run.closing:
                    return False
                messages += 1
                interrupted |= await self._on_message(run, message, user, model)
            if messages == 0:
                # The server closed the socket without an error: the session is dead.
                self._log.warning("an empty turn: the session is dead, reconnecting")
                if not await self._reconnect(run):
                    return False
                continue
            await self._end_turn(run, user, model, interrupted)
            if run.go_away or time.monotonic() - run.started >= self._reconnect_after:
                self._log.info("reconnecting before the server closes the session")
                if not await self._reconnect(run):
                    return False
        return False

    async def _on_message(
        self, run: _Run, message: Any, user: list[str], model: list[str]
    ) -> bool:
        """Report one server message. ``True`` when it says the caller interrupted."""
        session = run.session
        content = getattr(message, "server_content", None)
        audio, _text = convert.extract_payload(message)
        tool_call = getattr(message, "tool_call", None)
        output = getattr(content, "output_transcription", None) if content else None
        if audio or output or tool_call or getattr(content, "model_turn", None):
            run.generating = True
        interrupted = bool(getattr(content, "interrupted", None)) if content else False
        if interrupted:
            await session.interrupted()
        if audio:
            await session.audio(audio, self.output_rate)
        if content is not None:
            heard = getattr(content, "input_transcription", None)
            if heard is not None and getattr(heard, "text", None):
                user.append(heard.text)
            if output is not None and getattr(output, "text", None):
                model.append(output.text)
        for call in getattr(tool_call, "function_calls", None) or []:
            await session.tool_call(
                str(getattr(call, "id", "")),
                str(getattr(call, "name", "")),
                dict(getattr(call, "args", None) or {}),
            )
        update = getattr(message, "session_resumption_update", None)
        if update is not None:
            handle = getattr(update, "new_handle", None)
            if getattr(update, "resumable", False) and handle:
                run.handle = str(handle)
        if getattr(message, "go_away", None) is not None:
            run.go_away = True
        return interrupted

    async def _end_turn(
        self, run: _Run, user: list[str], model: list[str], interrupted: bool
    ) -> None:
        heard = convert.clean_transcript(user)
        said = "" if interrupted else convert.clean_transcript(model)
        # A cut-off answer is dropped: the model answers again, in full.
        if heard:
            run.log.append(("user", heard))
            await run.session.transcript(heard, "input")
        if said:
            run.log.append(("you", said))
            await run.session.transcript(said, "output")
        await run.session.turn_complete()
        run.generating = False
        pending, run.pending = run.pending, []
        if pending:
            await self._say(run, "[System Context Update] " + " ".join(pending))

    # ----------------------------------------------------------------------- send

    async def _send(self, run: _Run) -> None:
        """Send the caller's audio every flush interval; with manual detection tell the
        model when the caller starts and stops speaking."""
        silence_ticks = max(1, int(run.settings.silence_seconds / self._flush_interval))
        idle = 0
        try:
            while not run.closing:
                await asyncio.sleep(self._flush_interval)
                if run.reconnecting or run.conn is None:
                    continue
                if not run.buffer:
                    idle += 1
                    if run.settings.manual_vad and run.user_speaking and idle == silence_ticks:
                        if await self._try(run, activity_end=self._wire.activity_end()):
                            run.user_speaking = False
                    continue
                chunk, run.buffer = bytes(run.buffer), bytearray()
                idle = 0
                if run.settings.manual_vad and not run.user_speaking:
                    if await self._try(run, activity_start=self._wire.activity_start()):
                        run.user_speaking = True
                await self._try(
                    run, audio=self._wire.blob(chunk, convert.INPUT_MIME)
                )
        except asyncio.CancelledError:
            raise

    async def _try(self, run: _Run, **parts: Any) -> bool:
        conn = run.conn
        if conn is None:
            return False
        try:
            await conn.send_realtime_input(**parts)
        except Exception as exc:  # noqa: BLE001 - the receive task notices a dead socket
            self._log.debug(f"realtime input was not sent: {exc}")
            return False
        return True
