"""Voice channels and calls for the Discord bot.

The Core runs the call (hearing, the turn, the voice); this module carries the sound
(see ``core/extensions/calls.py`` for the other side):

    what people say  -> :class:`voice.CallSink` -> ``ctx.calls.audio`` -> the Core
    what the SyntH says <- ``ctx.calls.on_play`` -> :class:`voice.AudioBuffer` -> Discord

A call is the bot sitting in a voice channel with a sink listening and one always-on
source speaking. It starts when the model uses ``join_voice_discord`` or a person is
called (``start_call``), and ends when the Core ends it, when everybody leaves, or with
``leave_voice_discord``.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import convert
import discord
import voice

from synth_sdk.runtime import Context


class VoiceHost:
    """The voice channels the bot is in, one state per guild."""

    def __init__(self, ctx: Context, client: discord.Client) -> None:
        self.ctx = ctx
        self.client = client
        self.states: dict[int, dict[str, Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        voice.apply_patches()
        self.ctx.calls.on_play(self.on_play)
        self.ctx.calls.on_ended(self.on_core_ended)
        self.ctx.interfaces.on_call_control(self.dial, self.hang_up)

    async def stop(self) -> None:
        for guild_id in list(self.states):
            await self.leave(guild_id)
        for task in list(self._tasks):
            task.cancel()

    # --------------------------------------------------------------- channels

    async def _voice_channel(self, channel_id: str | int) -> Any:
        number = int(channel_id)
        channel = self.client.get_channel(number)
        if channel is None:
            with contextlib.suppress(discord.HTTPException):
                channel = await self.client.fetch_channel(number)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return None
        return channel

    def voice_channel_of(self, user_id: int | str) -> Any:
        """The voice channel a person is in right now (in any guild the bot shares)."""
        number = int(user_id)
        for guild in self.client.guilds:
            member = guild.get_member(number)
            state = getattr(member, "voice", None)
            if state is not None and getattr(state, "channel", None) is not None:
                return state.channel
        return None

    async def join(self, channel_id: str | int) -> dict[str, str]:
        """Sit in a voice channel (move there if the bot is in another of the guild)."""
        try:
            channel = await self._voice_channel(channel_id)
            if channel is None:
                return {
                    "status": "failed",
                    "message": f"{channel_id} is not a voice channel",
                }
            client = channel.guild.voice_client
            if client is not None:
                if client.channel.id == channel.id:
                    return {"status": "success", "message": "Already in the channel"}
                await client.move_to(channel)
                return {"status": "success", "message": f"Moved to {channel.name}"}
            if voice.HAS_VOICE_RECV:
                await channel.connect(cls=voice.voice_recv.VoiceRecvClient)
            else:
                await channel.connect()
            return {"status": "success", "message": f"Connected to {channel.name}"}
        except Exception as exc:  # noqa: BLE001 - reported to the model, never raised
            self.ctx.log.error(f"join voice {channel_id}: {type(exc).__name__}: {exc}")
            return {"status": "failed", "message": f"{type(exc).__name__}: {exc}"[:200]}

    async def leave(self, guild_id: int | str) -> dict[str, str]:
        """End the call of a guild, if any, and leave its voice channel."""
        number = int(guild_id)
        await self.stop_call(number)
        guild = self.client.get_guild(number)
        client = None if guild is None else guild.voice_client
        if client is None:
            return {"status": "success", "message": "Not in a voice channel"}
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "message": f"{type(exc).__name__}: {exc}"[:200]}
        return {"status": "success", "message": "Disconnected from voice"}

    # ------------------------------------------------------------------ calls

    async def start_call(
        self, channel_id: str | int, initial_user: str | None = None
    ) -> dict[str, str]:
        """Join the channel as a call. Returns ``{"status", "message"}``."""
        if not voice.HAS_VOICE_RECV:
            return {
                "status": "failed",
                "message": "voice receive support is not bundled",
            }
        joined = await self.join(channel_id)
        if joined["status"] != "success":
            return joined
        channel = await self._voice_channel(channel_id)
        client = None if channel is None else channel.guild.voice_client
        if channel is None or client is None:
            return {"status": "failed", "message": "Not connected to voice"}
        guild_id = channel.guild.id
        existing = self.states.get(guild_id)
        if existing is not None and existing["channel_id"] == channel.id:
            return {"status": "success", "message": "Call already active"}
        if existing is not None:
            await self.stop_call(guild_id)
        path = convert.build_path(guild_id, channel.id)
        buffer = voice.AudioBuffer()
        if client.is_playing():
            client.stop()
        client.play(voice.PCMSource(buffer))
        state: dict[str, Any] = {
            "channel_id": channel.id,
            "path": path,
            "buffer": buffer,
            "call_id": None,
            "user_id": initial_user,
        }
        # The answering tone arrives (on_play) before open() returns: the state is
        # already there for it to be found.
        self.states[guild_id] = state
        try:
            opened = await self.ctx.calls.open(
                path, str(initial_user or ""), kind="audio", announce=True
            )
        except Exception as exc:  # noqa: BLE001
            self.states.pop(guild_id, None)
            buffer.close()
            self.ctx.log.error(f"the call was not opened: {type(exc).__name__}: {exc}")
            return {"status": "failed", "message": f"Call not started: {exc}"[:200]}
        state["call_id"] = opened["call_id"]
        client.listen(voice.CallSink(self._emit(state)))
        self.ctx.log.info(f"call started in guild {guild_id} channel {channel.name}")
        return {"status": "success", "message": f"Call started in {channel.name}"}

    def _emit(self, state: dict[str, Any]) -> Any:
        """The thread-safe hand-over of a chunk from the router thread to the Core."""

        def emit(speaker_id: str, speaker_name: str, pcm: bytes) -> None:
            call_id, loop = state.get("call_id"), self._loop
            if call_id is None or loop is None or loop.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(
                self.ctx.calls.audio(
                    call_id,
                    pcm,
                    rate=voice.CORE_RATE,
                    speaker_id=speaker_id,
                    speaker_name=speaker_name,
                ),
                loop,
            )
            future.add_done_callback(
                lambda f: f.exception()
            )  # a lost chunk is just lost

        return emit

    async def stop_call(self, guild_id: int) -> dict[str, str]:
        """Hang up (the bot stays in the channel)."""
        state = self.states.get(guild_id)
        if state is None:
            return {"status": "success", "message": "No call in this guild"}
        try:
            call_id = state.get("call_id")
            if call_id is not None:
                # Hang up first so the end tone still goes out through the open buffer.
                with contextlib.suppress(Exception):
                    if await self.ctx.calls.end(call_id):
                        await asyncio.sleep(0.6)
            self._release(guild_id)
            return {"status": "success", "message": "Call stopped"}
        except Exception as exc:  # noqa: BLE001
            self.ctx.log.error(f"stopping the call: {type(exc).__name__}: {exc}")
            return {"status": "failed", "message": f"{type(exc).__name__}: {exc}"[:200]}

    def _release(self, guild_id: int) -> None:
        """Forget a call locally: stop listening and playing, close the buffer."""
        state = self.states.pop(guild_id, None)
        if state is None:
            return
        guild = self.client.get_guild(guild_id)
        client = None if guild is None else guild.voice_client
        if client is not None:
            if hasattr(client, "stop_listening"):
                with contextlib.suppress(Exception):
                    client.stop_listening()
            with contextlib.suppress(Exception):
                if client.is_playing():
                    client.stop()
        state["buffer"].close()

    # ------------------------------------------------------- Core's call hooks

    def _state_of(self, call_id: str, user_id: str | None) -> dict[str, Any] | None:
        for state in self.states.values():
            if state.get("call_id") == call_id:
                return state
        # The tone that answers arrives before open() has returned the call id.
        for state in self.states.values():
            if state.get("call_id") is None:
                return state
        return None

    async def on_play(
        self,
        interface_id: str,
        call_id: str,
        user_id: str | None,
        path: Path,
        speech: bool,
        video: dict[str, int] | None = None,
    ) -> float:
        """The Core wants this file played into the call. The file is gone when this
        returns, so it is decoded into the buffer now."""
        state = self._state_of(call_id, user_id)
        if state is None:
            return 0.0
        pcm = await voice.decode_for_playback(path)
        state["buffer"].write(pcm)
        return voice.seconds_of(pcm)

    async def on_core_ended(
        self, interface_id: str, call_id: str, user_id: str | None
    ) -> None:
        """The Core ended the call: stop listening and playing (the bot stays)."""
        for guild_id, state in list(self.states.items()):
            if state.get("call_id") == call_id:
                self._release(guild_id)

    async def dial(self, interface_id: str, user_id: str) -> dict[str, str]:
        """Ring a person: join the voice channel they are in."""
        channel = self.voice_channel_of(user_id)
        if channel is None:
            return {
                "status": "failed",
                "message": "That person is not in a voice channel",
            }
        return await self.start_call(channel.id, str(user_id))

    async def hang_up(self, interface_id: str, user_id: str) -> dict[str, str]:
        for guild_id, state in list(self.states.items()):
            if state.get("user_id") in (None, "", str(user_id)):
                return await self.stop_call(guild_id)
        return {"status": "success", "message": "No call with that person"}

    # ------------------------------------------------------------ voice events

    def humans_in(self, channel: Any, leaving: int | None = None) -> list[Any]:
        return [
            m
            for m in getattr(channel, "members", [])
            if not getattr(m, "bot", False) and m.id != leaving
        ]

    async def on_voice_state_update(self, member: Any, before: Any, after: Any) -> None:
        """Clean up when the bot is taken out of the channel or everybody leaves."""
        bot = self.client.user
        guild = getattr(member, "guild", None)
        if bot is None or guild is None or guild.id not in self.states:
            return
        guild_id = guild.id
        if member.id == bot.id and before.channel is not None:
            if after.channel is None:
                await asyncio.sleep(0)  # a corrective event may follow a transient one
                me = guild.get_member(bot.id)
                if getattr(getattr(me, "voice", None), "channel", None) is not None:
                    return  # it was transient: the bot is still in a channel
                self._release(guild_id)
                return
            if before.channel.id != after.channel.id:
                self._release(guild_id)  # moved elsewhere: that call is over
                return
        state = self.states[guild_id]
        if before.channel is not None and state["channel_id"] == before.channel.id:
            after_id = getattr(after.channel, "id", None)
            actually_left = after.channel is None or after_id != before.channel.id
            leaving = member.id if actually_left else None
            if not self.humans_in(before.channel, leaving):
                await self.stop_call(guild_id)
