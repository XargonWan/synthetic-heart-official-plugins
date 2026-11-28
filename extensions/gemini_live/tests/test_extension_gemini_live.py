"""Gemini Live: the Core's neutral session turned into a Gemini socket, and what the model
does reported back. The Google library is replaced by a fake connection."""

from __future__ import annotations

import asyncio
import base64
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from synth_sdk.live import LiveSession

SOURCE = Path(__file__).resolve().parent.parent


def _load() -> tuple[Any, Any]:
    sys.path.insert(0, str(SOURCE))
    try:
        return importlib.import_module("convert"), importlib.import_module("engine")
    finally:
        sys.path.remove(str(SOURCE))
        for name in ("convert", "engine", "wire", "ext_main"):
            sys.modules.pop(name, None)


convert, engine = _load()

PCM = b"\x01\x00" * 2400  # 100 ms at 24 kHz


async def settle(seconds: float = 0.08) -> None:
    await asyncio.sleep(seconds)


# ------------------------------------------------------------------- conversion


class TestSettings:
    def test_defaults_and_bad_values(self) -> None:
        s = convert.settings_from({})
        assert (s.api_key, s.affective, s.proactive, s.manual_vad) == ("", False, False, True)
        assert (s.thinking_level, s.thinking_budget, s.silence_seconds) == ("minimal", 0, 1.5)
        bad = convert.settings_from(
            {
                "GEMINI_API_KEY": "  k  ",
                "GEMINI_LIVE_THINKING_LEVEL": "extreme",
                "GEMINI_LIVE_THINKING_BUDGET": "lots",
                "GEMINI_LIVE_SILENCE_SECONDS": 99,
                "GEMINI_LIVE_AFFECTIVE_DIALOG": "TRUE",
                "GEMINI_LIVE_VAD": "Server",
            }
        )
        assert bad.api_key == "k" and bad.thinking_level == "minimal"
        assert bad.thinking_budget == 0 and bad.silence_seconds == 10.0
        assert bad.affective is True and bad.manual_vad is False

    def test_the_older_model_is_used_only_for_what_3_1_cannot_do(self) -> None:
        assert convert.choose_model(convert.Settings()) == convert.MODEL
        assert convert.choose_model(convert.Settings(affective=True)) == convert.LEGACY_MODEL
        assert convert.choose_model(convert.Settings(proactive=True)) == convert.LEGACY_MODEL


class TestTools:
    def test_neutral_tools_become_gemini_declarations(self) -> None:
        tools = [
            {
                "name": "send_message",
                "description": "Say something",
                "parameters": [
                    {"name": "text", "type": "string", "description": "what", "required": True},
                    {"name": "mood", "type": "string", "enum": ["a", "b"], "required": False},
                    {"name": "tags", "type": "array", "required": False},
                    {
                        "name": "when",
                        "type": "object",
                        "required": False,
                        "schema": {
                            "type": "object",
                            "properties": {"at": {"type": "integer", "minimum": 0}},
                            "required": ["at"],
                        },
                    },
                ],
            },
            {"name": "", "description": "nameless"},
        ]
        (tool,) = convert.tool_declarations(tools)
        (decl,) = tool["function_declarations"]
        assert decl["name"] == "send_message"
        params = decl["parameters"]
        assert params["type"] == "OBJECT" and params["required"] == ["text"]
        assert params["properties"]["text"] == {"type": "STRING", "description": "what"}
        assert params["properties"]["mood"]["enum"] == ["a", "b"]
        assert params["properties"]["tags"] == {"type": "ARRAY", "items": {"type": "STRING"}}
        assert params["properties"]["when"] == {
            "type": "OBJECT",
            "properties": {"at": {"type": "INTEGER", "minimum": 0}},
            "required": ["at"],
        }

    def test_no_tools_is_none(self) -> None:
        assert convert.tool_declarations([]) is None
        assert convert.tool_declarations([{"name": ""}]) is None


class TestConfig:
    def config(self, **kw: Any) -> dict[str, Any]:
        settings = kw.pop("settings", convert.Settings(api_key="k"))
        return convert.build_config(
            settings=settings, system_instruction="You are Rei.", tools=[], **kw
        )

    def test_a_3_1_session(self) -> None:
        c = self.config(voice="Puck", resume_handle="h1")
        assert c["response_modalities"] == ["AUDIO"]
        assert c["system_instruction"] == "You are Rei."
        voice = c["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"]
        assert voice == "Puck"
        assert c["session_resumption"] == {"handle": "h1"}
        assert c["realtime_input_config"] == {"automatic_activity_detection": {"disabled": True}}
        assert c["thinking_config"] == {"thinking_level": "MINIMAL", "include_thoughts": False}
        assert "tools" not in c and "enable_affective_dialog" not in c

    def test_the_default_voice_and_server_side_detection(self) -> None:
        c = self.config(settings=convert.Settings(api_key="k", manual_vad=False))
        voice = c["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"]
        assert voice == convert.DEFAULT_VOICE
        assert "realtime_input_config" not in c
        assert c["session_resumption"] == {"handle": None}

    def test_the_2_5_model_gets_a_budget_and_its_extras(self) -> None:
        s = convert.Settings(api_key="k", affective=True, proactive=True, thinking_budget=256)
        c = self.config(settings=s)
        assert c["enable_affective_dialog"] is True
        assert c["proactivity"] == {"proactive_audio": True}
        assert c["thinking_config"] == {"thinking_budget": 256, "include_thoughts": False}
        assert "thinking_config" not in self.config(
            settings=convert.Settings(api_key="k", affective=True)
        )

    def test_the_real_sdk_accepts_what_is_built(self) -> None:
        types = pytest.importorskip("google.genai.types")
        tools = [{"name": "say", "description": "d", "parameters": [{"name": "t", "type": "string"}]}]
        for settings in (
            convert.Settings(api_key="k"),
            convert.Settings(api_key="k", affective=True, proactive=True, thinking_budget=64),
            convert.Settings(api_key="k", manual_vad=False, thinking_level="high"),
        ):
            config = convert.build_config(
                settings=settings, system_instruction="x", tools=tools, resume_handle="h"
            )
            parsed = types.LiveConnectConfig(**config)
            assert parsed.tools[0].function_declarations[0].name == "say"


class TestMessages:
    def test_audio_and_text_parts(self) -> None:
        part_audio = SimpleNamespace(
            inline_data=SimpleNamespace(mime_type="audio/pcm;rate=24000", data=b"\x01\x02"), text=None
        )
        part_b64 = SimpleNamespace(
            inline_data=SimpleNamespace(mime_type="", data=base64.b64encode(b"\x03\x04").decode()),
            text=None,
        )
        part_image = SimpleNamespace(
            inline_data=SimpleNamespace(mime_type="image/png", data=b"png"), text="caption"
        )
        message = SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=SimpleNamespace(parts=[part_audio, part_b64, part_image])
            ),
            data=None,
            text=None,
        )
        assert convert.extract_payload(message) == (b"\x01\x02\x03\x04", "caption")

    def test_the_sdk_shortcuts_are_the_fallback(self) -> None:
        message = SimpleNamespace(server_content=None, data=b"\x09\x09", text="hi")
        assert convert.extract_payload(message) == (b"\x09\x09", "hi")
        assert convert.extract_payload(SimpleNamespace()) == (None, None)

    def test_transcripts_and_history(self) -> None:
        assert convert.clean_transcript(["Hel", "lo ", " world"]) == "Hello world"
        assert convert.history_snippet([]) == ""
        snippet = convert.history_snippet([("user", "hi"), ("you", "hello"), ("user", " ")])
        assert snippet.endswith("user: hi\nyou: hello") and "Recent conversation" in snippet
        assert "Do NOT greet" in convert.kick_text(reconnect=True)
        assert "Greet" in convert.kick_text()


# ----------------------------------------------------------------------- engine


def message(**content: Any) -> SimpleNamespace:
    extra = {k: content.pop(k) for k in ("tool_call", "go_away", "session_resumption_update") if k in content}
    return SimpleNamespace(server_content=SimpleNamespace(**content), data=None, text=None, **extra)


def audio_message(pcm: bytes = PCM) -> SimpleNamespace:
    part = SimpleNamespace(inline_data=SimpleNamespace(mime_type="audio/pcm", data=pcm), text=None)
    return message(model_turn=SimpleNamespace(parts=[part]))


def say(text: str, side: str = "output") -> SimpleNamespace:
    return message(**{f"{'output' if side == 'output' else 'input'}_transcription": SimpleNamespace(text=text)})


class FakeConn:
    def __init__(self) -> None:
        self.turns: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def receive(self) -> Any:
        turn = await self.turns.get()
        if isinstance(turn, Exception):
            raise turn
        for item in turn:
            yield item

    async def send_realtime_input(self, **parts: Any) -> None:
        self.sent.append(("input", parts))

    async def send_tool_response(self, **parts: Any) -> None:
        self.sent.append(("tool", parts))

    def texts(self) -> list[str]:
        return [p["text"] for kind, p in self.sent if kind == "input" and "text" in p]


class FakeCM:
    def __init__(self, library: "FakeLibrary", conn: FakeConn) -> None:
        self.library, self.conn, self.exited = library, conn, False

    async def __aenter__(self) -> FakeConn:
        if self.library.fail:
            self.library.fail -= 1
            raise ConnectionError("offline")
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True


class FakeLibrary:
    def __init__(self) -> None:
        self.conns: list[FakeConn] = []
        self.cms: list[FakeCM] = []
        self.opened: list[tuple[str, dict[str, Any]]] = []
        self.fail = 0

    def connector(self, settings: Any) -> Callable[[str, dict[str, Any]], FakeCM]:
        def connect(model: str, config: dict[str, Any]) -> FakeCM:
            self.opened.append((model, config))
            conn = FakeConn()
            self.conns.append(conn)
            cm = FakeCM(self, conn)
            self.cms.append(cm)
            return cm

        return connect

    @property
    def conn(self) -> FakeConn:
        return self.conns[-1]


class FakeWire:
    def activity_start(self) -> str:
        return "start"

    def activity_end(self) -> str:
        return "end"

    def blob(self, data: bytes, mime_type: str) -> tuple[str, bytes, str]:
        return ("blob", data, mime_type)

    def function_response(self, call_id: str, name: str, response: dict[str, Any]) -> tuple[Any, ...]:
        return ("fr", call_id, name, response)


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, m: str) -> None: self.lines.append(m)  # noqa: E704
    def warning(self, m: str) -> None: self.lines.append(m)  # noqa: E704
    def debug(self, m: str) -> None: self.lines.append(m)  # noqa: E704


class Rig:
    def __init__(self, settings: Any) -> None:
        self.library = FakeLibrary()
        self.events: list[dict[str, Any]] = []
        self.log = Log()

        async def load() -> Any:
            return settings

        self.engine = engine.GeminiLiveEngine(
            load,
            self.library.connector,
            FakeWire(),
            self.log,
            retry_delay=0.01,
            flush_interval=0.01,
            reconnect_after=3600,
        )

    def session(self, **kw: Any) -> LiveSession:
        async def emit(params: dict[str, Any]) -> None:
            self.events.append(params)

        return LiveSession("s1", system_instruction="persona", tools=kw.pop("tools", []), _emit=emit, **kw)

    def kinds(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


@pytest.fixture
async def rig() -> Any:
    made = Rig(convert.Settings(api_key="k", silence_seconds=0.05))
    yield made
    for sid in list(made.engine._runs):  # noqa: SLF001
        await made.engine.close(sid)


class TestOpening:
    async def test_no_key_refuses_so_the_call_stays_classic(self) -> None:
        bare = Rig(convert.Settings(api_key=""))
        with pytest.raises(RuntimeError, match="API key"):
            await bare.engine.open(bare.session())
        assert bare.library.opened == []

    async def test_a_fresh_session_connects_greets_and_says_so(self, rig: Rig) -> None:
        await rig.engine.open(rig.session(voice="Puck", tools=[{"name": "say", "description": "d", "parameters": []}]))
        ((model, config),) = rig.library.opened
        assert model == convert.MODEL and config["system_instruction"] == "persona"
        assert config["tools"][0]["function_declarations"][0]["name"] == "say"
        assert rig.library.conn.texts() == ["[Session started. Greet naturally.]"]
        assert rig.of("state")[-1]["state"] == "connected"

    async def test_a_resumed_session_is_not_greeted_again(self, rig: Rig) -> None:
        await rig.engine.open(rig.session(resume_handle="h0"))
        assert rig.library.opened[0][1]["session_resumption"] == {"handle": "h0"}
        assert rig.library.conn.texts() == []

    async def test_a_connection_that_fails_raises_and_leaves_nothing(self, rig: Rig) -> None:
        rig.library.fail = 1
        with pytest.raises(ConnectionError):
            await rig.engine.open(rig.session())
        assert rig.engine._runs == {}  # noqa: SLF001


class TestListening:
    async def test_the_callers_audio_is_framed_with_manual_detection(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        await rig.engine.audio("s1", b"\x01\x00" * 100, 16000)
        await rig.engine.audio("s1", b"\x02\x00" * 100, 16000)
        await settle(0.03)
        sent = [p for kind, p in rig.library.conn.sent if kind == "input" and "text" not in p]
        assert sent[0] == {"activity_start": "start"}
        assert sent[1]["audio"][2] == convert.INPUT_MIME
        assert sent[1]["audio"][1] == b"\x01\x00" * 100 + b"\x02\x00" * 100
        await settle(0.15)  # silence: the turn is over
        assert {"activity_end": "end"} in [
            p for kind, p in rig.library.conn.sent if kind == "input"
        ]

    async def test_server_side_detection_sends_only_audio(self) -> None:
        r = Rig(convert.Settings(api_key="k", manual_vad=False))
        await r.engine.open(r.session())
        await r.engine.audio("s1", b"\x01\x00" * 50, 16000)
        await settle(0.1)
        parts = [p for kind, p in r.library.conn.sent if kind == "input" and "text" not in p]
        assert len(parts) == 1 and "audio" in parts[0]
        await r.engine.close("s1")

    async def test_audio_for_an_unknown_session_is_ignored(self, rig: Rig) -> None:
        await rig.engine.audio("nope", b"\x01\x00", 16000)
        await rig.engine.text("nope", "x", "context")
        await rig.engine.tool_result("nope", "c", "n", {})
        await rig.engine.close("nope")


class TestTurns:
    async def test_a_turn_is_reported_in_order(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        rig.library.conn.turns.put_nowait(
            [say("how are you", "input"), audio_message(), say("fine, "), say("thanks")]
        )
        await settle()
        assert rig.kinds()[-4:] == ["audio", "transcript", "transcript", "turn_complete"]
        audio = rig.of("audio")[0]
        assert base64.b64decode(audio["pcm"]) == PCM and audio["rate"] == 24000
        heard, said = rig.of("transcript")
        assert (heard["side"], heard["text"]) == ("input", "how are you")
        assert (said["side"], said["text"]) == ("output", "fine, thanks")

    async def test_an_interruption_is_reported_at_once_and_the_cut_answer_is_dropped(
        self, rig: Rig
    ) -> None:
        await rig.engine.open(rig.session())
        rig.library.conn.turns.put_nowait(
            [audio_message(), say("I was say"), message(interrupted=True), say("wait", "input")]
        )
        await settle()
        kinds = rig.kinds()
        assert "interrupted" in kinds and kinds.index("interrupted") < kinds.index("turn_complete")
        assert [t["side"] for t in rig.of("transcript")] == ["input"]

    async def test_a_tool_call_goes_out_and_its_result_comes_back_without_scheduling(
        self, rig: Rig
    ) -> None:
        await rig.engine.open(rig.session())
        call = SimpleNamespace(id="c1", name="say", args={"text": "hi"})
        rig.library.conn.turns.put_nowait([message(), SimpleNamespace(server_content=None, data=None, text=None, tool_call=SimpleNamespace(function_calls=[call]))])
        await settle()
        assert rig.of("tool_call") == [
            {"session_id": "s1", "type": "tool_call", "call_id": "c1", "name": "say", "args": {"text": "hi"}}
        ]
        await rig.engine.tool_result("s1", "c1", "say", {"status": "ok", "scheduling": "SILENT"})
        assert ("tool", {"function_responses": [("fr", "c1", "say", {"status": "ok"})]}) in rig.library.conn.sent

    async def test_context_waits_for_the_model_to_finish(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        run = rig.engine._runs["s1"]  # noqa: SLF001
        run.generating = True
        await rig.engine.text("s1", "Marco joined", "context")
        await rig.engine.text("s1", "Anna joined", "context")
        assert "[System Context Update] Marco joined" not in rig.library.conn.texts()
        rig.library.conn.turns.put_nowait([audio_message()])
        await settle()
        assert rig.library.conn.texts()[-1] == "[System Context Update] Marco joined Anna joined"

    async def test_a_context_line_is_sent_at_once_when_the_model_is_idle(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        await rig.engine.text("s1", "Marco joined", "context")
        await rig.engine.text("s1", "say hi to Marco", "turn")
        assert rig.library.conn.texts()[-2:] == [
            "[System Context Update] Marco joined",
            "say hi to Marco",
        ]


class TestReconnecting:
    async def test_go_away_resumes_with_the_handle_and_without_a_greeting(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        first = rig.library.conn
        update = SimpleNamespace(resumable=True, new_handle="h42")
        first.turns.put_nowait(
            [
                SimpleNamespace(server_content=None, data=None, text=None, session_resumption_update=update),
                SimpleNamespace(server_content=None, data=None, text=None, go_away=SimpleNamespace(time_left="5s")),
            ]
        )
        await settle()
        assert len(rig.library.conns) == 2 and rig.library.cms[0].exited
        assert rig.library.opened[1][1]["session_resumption"] == {"handle": "h42"}
        assert rig.library.conns[1].texts() == []
        states = [(e["state"], e["resume_handle"]) for e in rig.of("state")]
        assert states[-2:] == [("reconnecting", "h42"), ("connected", "h42")]
        # The new socket is the one listened to.
        rig.library.conns[1].turns.put_nowait([audio_message()])
        await settle()
        assert rig.of("audio")

    async def test_the_time_limit_reconnects_too(self) -> None:
        r = Rig(convert.Settings(api_key="k"))
        r.engine._reconnect_after = 0.0  # noqa: SLF001
        await r.engine.open(r.session())
        r.library.conn.turns.put_nowait([audio_message()])
        await settle()
        assert len(r.library.conns) == 2
        await r.engine.close("s1")

    async def test_an_empty_turn_without_a_handle_restarts_with_the_conversation(
        self, rig: Rig
    ) -> None:
        await rig.engine.open(rig.session())
        rig.library.conn.turns.put_nowait([say("my name is Marco", "input"), say("nice to meet you")])
        await settle()
        rig.library.conn.turns.put_nowait([])  # the server closed the socket
        await settle()
        assert len(rig.library.conns) == 2
        (kick,) = rig.library.conns[1].texts()
        assert "Do NOT greet" in kick and "user: my name is Marco" in kick and "you: nice to meet you" in kick

    async def test_a_dropped_connection_is_reported_and_restored(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        rig.library.conn.turns.put_nowait(ConnectionError("1011"))
        await settle()
        assert rig.of("error")[0]["detail"] == "1011"
        assert len(rig.library.conns) == 2 and rig.of("state")[-1]["state"] == "connected"

    async def test_a_session_that_cannot_be_restored_is_closed(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        rig.library.fail = 3
        rig.library.conn.turns.put_nowait([])
        await settle(0.3)
        assert rig.of("state")[-1]["state"] == "closed"
        assert "could not be restored" in rig.of("error")[-1]["detail"]

    async def test_audio_during_a_reconnect_is_dropped(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        rig.engine._runs["s1"].reconnecting = True  # noqa: SLF001
        await rig.engine.audio("s1", b"\x01\x00" * 10, 16000)
        assert rig.engine._runs["s1"].buffer == b""  # noqa: SLF001


class TestClosing:
    async def test_closing_disconnects_and_stops_the_tasks(self, rig: Rig) -> None:
        await rig.engine.open(rig.session())
        run = rig.engine._runs["s1"]  # noqa: SLF001
        await rig.engine.close("s1")
        assert rig.library.cms[0].exited and all(t.done() for t in run.tasks)
        assert rig.engine._runs == {}  # noqa: SLF001
        await rig.engine.close("s1")  # twice is harmless
