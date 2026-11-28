"""Discord voice: the audio plumbing, and a call from start to hang-up with a fake
voice client (discord.py's real voice connection needs a real server)."""

from __future__ import annotations

import audioop
import importlib
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parent.parent


def _load() -> tuple[Any, Any, Any]:
    sys.path.insert(0, str(SOURCE))
    try:
        modules = (
            importlib.import_module("voice"),
            importlib.import_module("calls"),
            importlib.import_module("ext_main"),
        )
        return modules
    finally:
        sys.path.remove(str(SOURCE))
        for name in ("convert", "ext_main", "calls", "voice"):
            sys.modules.pop(name, None)


voice, calls, ext_main = _load()
import discord  # noqa: E402


def tone(seconds: float = 0.1, rate: int = 48000, channels: int = 2) -> bytes:
    frames = int(seconds * rate)
    return (b"\x10\x00" * channels) * frames


class TestConversion:
    def test_discord_audio_becomes_16k_mono_chunks_of_100_ms(self) -> None:
        chunker = voice.Chunker()
        chunks = chunker.feed("1", tone(0.35))  # 350 ms from one speaker
        assert [len(c) for c in chunks] == [voice.CHUNK_BYTES] * 3
        assert voice.CHUNK_BYTES == 3200  # 100 ms of 16 kHz 16-bit mono

    def test_each_speaker_has_their_own_buffer(self) -> None:
        chunker = voice.Chunker()
        assert chunker.feed("a", tone(0.06)) == []  # not a whole chunk yet
        assert chunker.feed("b", tone(0.06)) == []
        assert len(chunker.feed("a", tone(0.06))) == 1  # 120 ms of a
        chunker.clear()
        assert chunker.feed("a", tone(0.06)) == []

    def test_the_left_channel_is_the_voice(self) -> None:
        left_only = b"\xff\x7f\x00\x00" * 4800  # loud left, silent right
        pcm, _ = voice.to_core_pcm(left_only)
        assert audioop.rms(pcm, 2) > 20000


class TestPlayback:
    def test_the_buffer_gives_frames_then_silence_and_knows_when_it_speaks(
        self,
    ) -> None:
        buffer = voice.AudioBuffer()
        source = voice.PCMSource(buffer)
        assert source.read() == b"\x00" * voice.FRAME_BYTES  # nothing yet: silence
        assert not buffer.is_speaking(cooldown=0)
        buffer.write(b"\x01\x00" * (voice.FRAME_BYTES // 2 + 100))
        assert buffer.is_speaking()
        assert source.read() == b"\x01\x00" * (voice.FRAME_BYTES // 2)
        tail = source.read()  # the partial frame is padded with silence
        assert len(tail) == voice.FRAME_BYTES and tail.endswith(b"\x00")
        assert source.is_opus() is False

    def test_a_closed_buffer_takes_nothing(self) -> None:
        buffer = voice.AudioBuffer()
        buffer.close()
        buffer.write(b"\x01\x00" * 100)
        assert buffer.read(10) == b""

    def write_wav(self, path: Path, rate: int, channels: int, seconds: float) -> None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(tone(seconds, rate, channels))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("rate", "channels"), [(24000, 1), (16000, 1), (48000, 2)])
    async def test_a_wav_becomes_48k_stereo_whatever_it_was(
        self, tmp_path: Path, rate: int, channels: int
    ) -> None:
        path = tmp_path / "x.wav"
        self.write_wav(path, rate, channels, 0.5)
        pcm = await voice.decode_for_playback(path)
        assert abs(voice.seconds_of(pcm) - 0.5) < 0.02
        assert len(pcm) % 4 == 0

    @pytest.mark.asyncio
    async def test_something_that_is_not_a_wav_needs_ffmpeg_and_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "x.ogg"
        path.write_bytes(b"OggS-not-really")
        monkeypatch.setattr(voice.shutil, "which", lambda name: None)
        with pytest.raises(RuntimeError, match="ffmpeg"):
            await voice.decode_for_playback(path)


class FakeVoiceClient:
    def __init__(self, channel: Any) -> None:
        self.channel = channel
        self.source: Any = None
        self.sink: Any = None
        self.disconnected = False

    def is_playing(self) -> bool:
        return self.source is not None

    def play(self, source: Any) -> None:
        self.source = source

    def stop(self) -> None:
        self.source = None

    def listen(self, sink: Any) -> None:
        self.sink = sink

    def stop_listening(self) -> None:
        self.sink = None

    async def move_to(self, channel: Any) -> None:
        self.channel = channel

    async def disconnect(self) -> None:
        self.disconnected = True


class Guild:
    def __init__(self, id: int, name: str = "Home") -> None:
        self.id, self.name = id, name
        self.voice_client: FakeVoiceClient | None = None
        self.members: dict[int, Any] = {}

    def get_member(self, number: int) -> Any:
        return self.members.get(number)


class VoiceChannel:
    def __init__(self, id: int, guild: Guild, name: str = "Lounge") -> None:
        self.id, self.guild, self.name = id, guild, name
        self.members: list[Any] = []

    async def connect(self, cls: Any = None) -> FakeVoiceClient:
        self.guild.voice_client = FakeVoiceClient(self)
        return self.guild.voice_client


class FakeClient:
    def __init__(self) -> None:
        self.user = SimpleNamespace(id=42, name="Rei")
        self.guilds: list[Guild] = []
        self.channels: dict[int, Any] = {}
        self.voice_clients: list[Any] = []

    def get_channel(self, number: int) -> Any:
        return self.channels.get(number)

    async def fetch_channel(self, number: int) -> Any:
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "no")

    def get_guild(self, number: int) -> Guild | None:
        return next((g for g in self.guilds if g.id == number), None)


class FakeCalls:
    def __init__(self) -> None:
        self.opened: list[tuple[Any, ...]] = []
        self.ended: list[str] = []
        self.audio_chunks: list[tuple[str, bytes, str]] = []
        self.on_play_during_open: Any = None
        self.provided: list[str] = []
        self.streamed: list[bool] = []
        self.mode = "classic"

    async def open(
        self, path: str, user_id: str, *, kind: str, announce: bool
    ) -> dict[str, Any]:
        self.opened.append((path, user_id, kind, announce))
        if self.on_play_during_open is not None:
            await (
                self.on_play_during_open()
            )  # the answering tone arrives before we return
        return {"call_id": "call-1", "mode": self.mode}

    async def end(self, call_id: str) -> bool:
        self.ended.append(call_id)
        return True

    async def audio(
        self, call_id: str, pcm: bytes, *, rate: int, speaker_id: str, speaker_name: str
    ) -> None:
        self.audio_chunks.append((call_id, pcm, speaker_id))

    async def provider(self, interface_id: str, stream: bool = False) -> None:
        self.provided.append(interface_id)
        self.streamed.append(stream)


class FakeInterfaces:
    def __init__(self, trainer: str | None) -> None:
        self._trainer = trainer
        self.handlers: dict[str, Any] = {}

    async def trainer(self, interface_id: str) -> str | None:
        return self._trainer

    def on_call_control(self, start: Any, end: Any) -> None:
        self.handlers["call_control"] = (start, end)


class Ctx:
    def __init__(self, trainer: str | None = "7", trainer_only: bool = True) -> None:
        self.calls = FakeCalls()
        self.interfaces = FakeInterfaces(trainer)
        self.config = SimpleNamespace(get=self._get)
        self._trainer_only = trainer_only
        self.log = SimpleNamespace(
            info=lambda m: None, warning=lambda m: None, error=lambda m: None
        )

    async def _get(self, key: str) -> Any:
        return self._trainer_only if key == "DISCORD_VOICE_TRAINER_ONLY" else None


@pytest.fixture
def world() -> SimpleNamespace:
    client = FakeClient()
    guild = Guild(11)
    channel = VoiceChannel(55, guild)
    client.guilds.append(guild)
    client.channels[55] = channel
    # the check "is this a voice channel" is an isinstance on discord's classes
    return SimpleNamespace(client=client, guild=guild, channel=channel)


@pytest.fixture(autouse=True)
def real_looking_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discord, "VoiceChannel", VoiceChannel)
    monkeypatch.setattr(calls.voice, "HAS_VOICE_RECV", True)


def host(world: SimpleNamespace, ctx: Ctx | None = None) -> Any:
    return calls.VoiceHost(ctx or Ctx(), world.client)


class TestCall:
    @pytest.mark.asyncio
    async def test_a_call_joins_listens_speaks_and_hangs_up(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sunk: dict[str, Any] = {}
        monkeypatch.setattr(
            calls.voice,
            "CallSink",
            lambda emit: sunk.setdefault("emit", emit) or object(),
        )
        h = host(world)
        h._loop = __import__("asyncio").get_running_loop()  # noqa: SLF001
        result = await h.start_call(55, "7")
        assert result["status"] == "success"
        client = world.guild.voice_client
        assert (
            client.source is not None and client.sink is not None
        )  # speaking + listening
        assert h.ctx.calls.opened == [("discord_bot/11/55", "7", "audio", True)]
        state = h.states[11]
        assert state["call_id"] == "call-1"
        # what the Core wants played is decoded into the always-on buffer
        wav = Path(__file__).parent / "_tone.wav"
        try:
            with wave.open(str(wav), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(24000)
                out.writeframes(b"\x10\x00" * 12000)  # 0.5 s
            seconds = await h.on_play("discord_bot", "call-1", "7", wav, True)
        finally:
            wav.unlink(missing_ok=True)
        assert abs(seconds - 0.5) < 0.02
        assert state["buffer"].pending_seconds() == pytest.approx(seconds, abs=0.001)
        # hang up: the Core is told, the bot stays in the channel
        assert (await h.stop_call(11))["status"] == "success"
        assert h.ctx.calls.ended == ["call-1"] and 11 not in h.states
        assert world.guild.voice_client.disconnected is False

    @pytest.mark.asyncio
    async def test_the_answering_tone_is_played_before_open_returns(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)
        played: list[float] = []

        async def tone_arrives() -> None:
            wav = Path(__file__).parent / "_tone2.wav"
            with wave.open(str(wav), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(24000)
                out.writeframes(b"\x10\x00" * 2400)
            try:
                played.append(await h.on_play("discord_bot", "call-1", "7", wav, False))
            finally:
                wav.unlink(missing_ok=True)

        h.ctx.calls.on_play_during_open = tone_arrives
        await h.start_call(55, "7")
        assert played and played[0] == pytest.approx(0.1, abs=0.01)

    @pytest.mark.asyncio
    async def test_a_failed_open_leaves_nothing_behind(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)

        async def refuse(*a: Any, **k: Any) -> Any:
            raise RuntimeError("no call service")

        h.ctx.calls.open = refuse
        result = await h.start_call(55, "7")
        assert result["status"] == "failed" and h.states == {}

    @pytest.mark.asyncio
    async def test_something_that_is_not_a_voice_channel_is_refused(
        self, world: SimpleNamespace
    ) -> None:
        result = await host(world).start_call(999, "7")
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_the_core_ending_the_call_stops_it_here_but_the_bot_stays(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)
        await h.start_call(55, "7")
        await h.on_core_ended("discord_bot", "call-1", "7")
        assert h.states == {} and world.guild.voice_client.sink is None
        assert world.guild.voice_client.disconnected is False

    @pytest.mark.asyncio
    async def test_leaving_ends_the_call_and_disconnects(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)
        await h.start_call(55, "7")
        assert (await h.leave(11))["status"] == "success"
        assert world.guild.voice_client.disconnected is True and h.ctx.calls.ended == [
            "call-1"
        ]

    @pytest.mark.asyncio
    async def test_dialling_goes_to_where_the_person_is(
        self, world: SimpleNamespace
    ) -> None:
        world.guild.members[7] = SimpleNamespace(
            voice=SimpleNamespace(channel=world.channel)
        )
        h = host(world)
        assert (await h.dial("discord_bot", "7"))["status"] == "success"
        world.guild.members[7] = SimpleNamespace(voice=None)
        assert (await h.dial("discord_bot", "7"))["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_chunk_from_the_router_thread_reaches_the_core(
        self, world: SimpleNamespace
    ) -> None:
        import asyncio

        h = host(world)
        h._loop = asyncio.get_running_loop()  # noqa: SLF001
        state = {"call_id": "call-1"}
        h._emit(state)("7", "Alice", b"\x00" * 3200)  # noqa: SLF001
        await asyncio.sleep(0.05)
        assert h.ctx.calls.audio_chunks == [("call-1", b"\x00" * 3200, "7")]
        state["call_id"] = None  # not open yet: dropped, not an error
        h._emit(state)("7", "Alice", b"\x00" * 3200)  # noqa: SLF001
        await asyncio.sleep(0.05)
        assert len(h.ctx.calls.audio_chunks) == 1


class TestLiveMode:
    """A Live engine holds the call: its voice arrives as chunks, and a barge-in drops them."""

    def chunk(self, seconds: float = 0.1, rate: int = 24000) -> bytes:
        return b"\x10\x00" * int(seconds * rate)

    def test_the_models_voice_becomes_48_khz_stereo_and_joins_without_a_gap(self) -> None:
        first, state = voice.stream_to_playback(self.chunk(), 24000)
        second, state = voice.stream_to_playback(self.chunk(), 24000, state)
        assert len(first) == pytest.approx(voice.RATE * 0.1 * 4, abs=16)
        assert voice.seconds_of(first + second) == pytest.approx(0.2, abs=0.002)
        assert state is not None
        # a half sample at the end of a chunk is dropped, not played as noise
        odd, _ = voice.stream_to_playback(self.chunk() + b"\x01", 24000)
        assert len(odd) % 4 == 0
        # 48 kHz mono needs no resampling, only the second channel
        same, none_state = voice.stream_to_playback(b"\x02\x00" * 10, 48000)
        assert same == b"\x02\x00\x02\x00" * 10 and none_state is None

    def test_clearing_the_buffer_drops_what_is_queued(self) -> None:
        buffer = voice.AudioBuffer()
        buffer.write(b"\x01\x00" * 4800)
        assert buffer.pending_seconds() > 0
        buffer.clear()
        assert buffer.pending_seconds() == 0 and buffer.read(100) == b""

    @pytest.mark.asyncio
    async def test_a_live_call_plays_the_stream_and_a_barge_in_empties_it(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(calls.voice, "CallSink", lambda emit: object())
        h = host(world)
        h._loop = __import__("asyncio").get_running_loop()  # noqa: SLF001
        h.ctx.calls.mode = "live"
        await h.start_call(55, "7")
        state = h.states[11]
        assert state["mode"] == "live"
        await h.on_stream("discord_bot", "call-1", self.chunk(0.25), 24000)
        await h.on_stream("discord_bot", "call-1", self.chunk(0.25), 24000)
        assert state["buffer"].pending_seconds() == pytest.approx(0.5, abs=0.01)
        await h.on_flush("discord_bot", "call-1")
        assert state["buffer"].pending_seconds() == 0 and state["resample"] is None
        # what comes after the interruption plays normally
        await h.on_stream("discord_bot", "call-1", self.chunk(0.1), 24000)
        assert state["buffer"].pending_seconds() == pytest.approx(0.1, abs=0.01)

    @pytest.mark.asyncio
    async def test_a_classic_call_is_marked_so_and_a_stray_stream_is_ignored(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(calls.voice, "CallSink", lambda emit: object())
        h = host(world)
        h._loop = __import__("asyncio").get_running_loop()  # noqa: SLF001
        await h.start_call(55, "7")
        assert h.states[11]["mode"] == "classic"
        await h.on_stream("discord_bot", "no-such-call", self.chunk(), 24000)
        await h.on_flush("discord_bot", "no-such-call")
        assert h.states[11]["buffer"].pending_seconds() == 0.0


class TestVoiceEvents:
    def member(self, id: int, bot: bool = False) -> Any:
        return SimpleNamespace(id=id, bot=bot, guild=None)

    @pytest.mark.asyncio
    async def test_the_last_human_leaving_ends_the_call(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)
        await h.start_call(55, "7")
        alice = self.member(7)
        alice.guild = world.guild
        world.channel.members = [alice]
        await h.on_voice_state_update(
            alice, SimpleNamespace(channel=world.channel), SimpleNamespace(channel=None)
        )
        assert h.states == {} and h.ctx.calls.ended == ["call-1"]

    @pytest.mark.asyncio
    async def test_muting_does_not_end_it(self, world: SimpleNamespace) -> None:
        h = host(world)
        await h.start_call(55, "7")
        alice = self.member(7)
        alice.guild = world.guild
        world.channel.members = [alice]
        await h.on_voice_state_update(
            alice,
            SimpleNamespace(channel=world.channel),
            SimpleNamespace(channel=world.channel),
        )
        assert 11 in h.states

    @pytest.mark.asyncio
    async def test_the_bot_being_moved_out_ends_the_call_without_telling_the_core(
        self, world: SimpleNamespace
    ) -> None:
        h = host(world)
        await h.start_call(55, "7")
        other = VoiceChannel(56, world.guild, "Other")
        bot = SimpleNamespace(id=42, bot=True, guild=world.guild)
        await h.on_voice_state_update(
            bot, SimpleNamespace(channel=world.channel), SimpleNamespace(channel=other)
        )
        assert 11 not in h.states


class TestActions:
    def bridge(self, world: SimpleNamespace, **kw: Any) -> Any:
        ctx = Ctx(**kw)
        ctx.stats = SimpleNamespace(incr=lambda *a, **k: None)
        return ext_main.Bridge(ctx, world.client)

    @pytest.mark.asyncio
    async def test_the_trainer_can_ask_it_to_join_where_they_are(
        self, world: SimpleNamespace
    ) -> None:
        bridge = self.bridge(world)
        bridge.last_speaker["discord_bot/11/22"] = ("7", "55")
        result = await bridge.on_action(
            "discord_bot",
            "join_voice_discord",
            {"interface_path": "discord_bot/11/22"},
            {},
        )
        assert result["status"] == "success" and 11 in bridge.voice.states

    @pytest.mark.asyncio
    async def test_someone_else_cannot_unless_the_operator_opened_it(
        self, world: SimpleNamespace
    ) -> None:
        bridge = self.bridge(world)
        bridge.last_speaker["discord_bot/11/22"] = ("99", "55")
        result = await bridge.on_action(
            "discord_bot",
            "join_voice_discord",
            {"interface_path": "discord_bot/11/22"},
            {},
        )
        assert result["status"] == "failed" and "trainer" in result["message"]
        open_bridge = self.bridge(world, trainer_only=False)
        open_bridge.last_speaker["discord_bot/11/22"] = ("99", "55")
        done = await open_bridge.on_action(
            "discord_bot",
            "join_voice_discord",
            {"interface_path": "discord_bot/11/22"},
            {},
        )
        assert done["status"] == "success"

    @pytest.mark.asyncio
    async def test_the_servers_id_given_as_a_channel_is_ignored(
        self, world: SimpleNamespace
    ) -> None:
        bridge = self.bridge(world)
        bridge.last_speaker["discord_bot/11/22"] = ("7", "55")
        result = await bridge.on_action(
            "discord_bot",
            "join_voice_discord",
            {"interface_path": "discord_bot/11/22", "channel_id": "11"},
            {},
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_with_nobody_in_voice_it_says_so(
        self, world: SimpleNamespace
    ) -> None:
        bridge = self.bridge(world)
        bridge.last_speaker["discord_bot/11/22"] = ("7", None)
        result = await bridge.on_action(
            "discord_bot",
            "join_voice_discord",
            {"interface_path": "discord_bot/11/22"},
            {},
        )
        assert result["status"] == "failed" and "voice channel" in result["message"]

    @pytest.mark.asyncio
    async def test_leave_by_path_or_guild_and_unknown_actions(
        self, world: SimpleNamespace
    ) -> None:
        bridge = self.bridge(world)
        await bridge.voice.start_call(55, "7")
        left = await bridge.on_action(
            "discord_bot",
            "leave_voice_discord",
            {"interface_path": "discord_bot/11/22"},
            {},
        )
        assert left["status"] == "success" and world.guild.voice_client.disconnected
        assert (await bridge.on_action("discord_bot", "leave_voice_discord", {}, {}))[
            "status"
        ] == "failed"
        assert (await bridge.on_action("discord_bot", "dance", {}, {}))[
            "status"
        ] == "failed"


def test_the_resilience_patches_apply_once_when_voice_recv_is_there() -> None:
    if not voice.HAS_VOICE_RECV:
        pytest.skip("discord-ext-voice-recv is not installed here")
    assert voice.apply_patches() is True
    assert voice.apply_patches() is True  # idempotent


def test_the_rtcp_crypto_noise_is_filtered_out_of_the_log() -> None:
    import logging

    record = logging.LogRecord(
        "x", logging.ERROR, "f", 1, "CryptoError decoding packet data", None, None
    )
    other = logging.LogRecord("x", logging.ERROR, "f", 1, "something else", None, None)
    noise = voice._CryptoErrorFilter()
    assert noise.filter(record) is False and noise.filter(other) is True
