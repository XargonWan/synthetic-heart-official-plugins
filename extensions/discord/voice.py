"""Audio plumbing for Discord voice channels (discord.py + discord-ext-voice-recv).

Discord speaks 48 kHz stereo 16-bit PCM in 20 ms frames; the Core's call service takes
16 kHz mono in chunks of about 100 ms. This module converts between the two and holds the
discord.py-facing classes (an always-on playback source, a receiving sink). Nothing here
talks to the Core.

``voice_recv`` is optional: without it the bot can still speak, but not listen.
"""

from __future__ import annotations

import asyncio
import audioop
import logging
import shutil
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import discord

try:  # the receiving half of voice
    from discord.ext import voice_recv

    HAS_VOICE_RECV = True
except ImportError:  # pragma: no cover - depends on what is bundled
    voice_recv = None  # type: ignore[assignment]
    HAS_VOICE_RECV = False

RATE = 48000
WIDTH = 2
#: 20 ms of 48 kHz stereo 16-bit audio.
FRAME_BYTES = 3840
#: What the Core's call service takes: 16 kHz mono, about 100 ms at a time.
CORE_RATE = 16000
CHUNK_BYTES = int(CORE_RATE * WIDTH * 0.1)

log = logging.getLogger("discord.ext.voice_recv")

# ----------------------------------------------------------------- resilience patches

_decode_stats = {"count": 0, "errors": 0, "dave_ok": 0, "dave_fail": 0}
_SILENCE_FRAME = b"\x00" * FRAME_BYTES
_patched = False


class _CryptoErrorFilter(logging.Filter):
    """Drop voice_recv's "CryptoError decoding packet data" lines.

    Discord periodically sends RTCP feedback packets that the library tries to decrypt and
    cannot (a known upstream limitation); it drops them silently, so the ERROR it logs is
    noise that would fill the Core's log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "CryptoError" not in record.getMessage()


def _quiet_rtcp_noise() -> None:
    reader = logging.getLogger("discord.ext.voice_recv.reader")
    if not any(isinstance(f, _CryptoErrorFilter) for f in reader.filters):
        reader.addFilter(_CryptoErrorFilter())


def apply_patches() -> bool:
    """Make voice_recv survive what real channels throw at it. Idempotent.

    voice_recv's decoder is better than any we would write (jitter buffer, FEC, loss
    concealment), but one unhandled exception in its packet-router thread ends audio
    receive for good, and it knows nothing of DAVE, the end-to-end encryption Discord
    puts on top of the transport encryption. So:

    1. ``PacketDecoder._decode_packet``: DAVE-decrypt first, and turn an Opus error into
       a concealment frame instead of an exception;
    2. ``PacketRouter._do_run``: catch errors per iteration instead of dying.
    """
    global _patched
    if _patched or not HAS_VOICE_RECV:
        return _patched
    _quiet_rtcp_noise()
    from discord.ext.voice_recv.opus import PacketDecoder
    from discord.ext.voice_recv.router import PacketRouter

    try:
        import davey

        media_audio = davey.MediaType.audio
        has_davey = True
    except ImportError:  # pragma: no cover - depends on what is bundled
        media_audio, has_davey = None, False

    original = PacketDecoder._decode_packet

    def safe_decode_packet(self: Any, packet: Any) -> tuple[Any, bytes]:
        _decode_stats["count"] += 1
        if has_davey and packet and getattr(packet, "decrypted_data", None):
            connection = getattr(
                getattr(self.sink, "voice_client", None), "_connection", None
            )
            session = getattr(connection, "dave_session", None)
            user_id = self._cached_id
            if session is not None and user_id is not None:
                try:
                    packet.decrypted_data = session.decrypt(
                        user_id, media_audio, bytes(packet.decrypted_data)
                    )
                    _decode_stats["dave_ok"] += 1
                except Exception as exc:  # noqa: BLE001 - the Opus decode below conceals it
                    _decode_stats["dave_fail"] += 1
                    if _decode_stats["dave_fail"] <= 10:
                        log.warning(
                            "DAVE decrypt failed: %s: %s", type(exc).__name__, exc
                        )
        try:
            return original(self, packet)
        except Exception as exc:  # noqa: BLE001
            _decode_stats["errors"] += 1
            errors = _decode_stats["errors"]
            if errors <= 5 or errors % 500 == 0:
                log.warning(
                    "Opus decode error #%d: %s: %s", errors, type(exc).__name__, exc
                )
            try:
                if self._decoder is not None:
                    return packet, self._decoder.decode(None, fec=False)
            except Exception:  # noqa: BLE001
                pass
            return packet, _SILENCE_FRAME

    def resilient_do_run(self: Any) -> None:
        consecutive = 0
        while not self._end_thread.is_set():
            try:
                self.waiter.wait()
                with self._lock:
                    for decoder in self.waiter.items:
                        try:
                            data = decoder.pop_data()
                            if data is not None:
                                self.sink.write(data.source, data)
                        except Exception:  # noqa: BLE001
                            consecutive += 1
                            if consecutive <= 5 or consecutive % 100 == 0:
                                log.warning(
                                    "error #%d in decode/write, continuing",
                                    consecutive,
                                    exc_info=True,
                                )
            except Exception:  # noqa: BLE001
                consecutive += 1
                if consecutive <= 5 or consecutive % 100 == 0:
                    log.warning(
                        "error #%d in the router loop, continuing",
                        consecutive,
                        exc_info=True,
                    )
            else:
                consecutive = 0

    PacketDecoder._decode_packet = safe_decode_packet  # type: ignore[method-assign]
    PacketRouter._do_run = resilient_do_run  # type: ignore[method-assign]
    _patched = True
    return True


# ------------------------------------------------------------------------ conversion


def to_core_pcm(stereo48k: bytes, state: Any = None) -> tuple[bytes, Any]:
    """48 kHz stereo -> 16 kHz mono (speech is in the left channel); ``state`` carries
    the resampler across packets of one speaker."""
    mono = audioop.tomono(stereo48k, WIDTH, 1, 0)
    return audioop.ratecv(mono, WIDTH, 1, RATE, CORE_RATE, state)


class Chunker:
    """Gathers each speaker's audio into ~100 ms chunks for the Core."""

    def __init__(self) -> None:
        self._buffers: dict[str, bytearray] = {}
        self._states: dict[str, Any] = {}

    def feed(self, speaker: str, stereo48k: bytes) -> list[bytes]:
        pcm, self._states[speaker] = to_core_pcm(stereo48k, self._states.get(speaker))
        buffer = self._buffers.setdefault(speaker, bytearray())
        buffer.extend(pcm)
        chunks: list[bytes] = []
        while len(buffer) >= CHUNK_BYTES:
            chunks.append(bytes(buffer[:CHUNK_BYTES]))
            del buffer[:CHUNK_BYTES]
        return chunks

    def clear(self) -> None:
        self._buffers.clear()
        self._states.clear()


# -------------------------------------------------------------------------- playback


class AudioBuffer:
    """Thread-safe queue of 48 kHz stereo PCM: ``write`` is called from asyncio, ``read``
    from discord.py's voice send thread."""

    def __init__(self) -> None:
        self._data = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        self._last_real_read = 0.0

    def write(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        with self._lock:
            self._data.extend(pcm)

    def read(self, nbytes: int) -> bytes:
        with self._lock:
            if not self._data:
                return b""
            chunk = bytes(self._data[:nbytes])
            del self._data[:nbytes]
        self._last_real_read = time.monotonic()
        return chunk

    def clear(self) -> None:
        """Drop everything queued and not yet played (the caller spoke over the SyntH)."""
        with self._lock:
            self._data.clear()

    def pending_seconds(self) -> float:
        with self._lock:
            return len(self._data) / (RATE * WIDTH * 2)

    def is_speaking(self, cooldown: float = 0.5) -> bool:
        if self._data:
            return True
        return (time.monotonic() - self._last_real_read) < cooldown

    def close(self) -> None:
        self._closed = True
        with self._lock:
            self._data.clear()


class PCMSource(discord.AudioSource):
    """One always-on source: every sound (tones, the SyntH's voice) is written into the
    buffer, so nothing ever cuts anything else off. Silence keeps it alive."""

    def __init__(self, buffer: AudioBuffer) -> None:
        self._buffer = buffer

    def read(self) -> bytes:
        data = self._buffer.read(FRAME_BYTES)
        if not data:
            return _SILENCE_FRAME
        if len(data) < FRAME_BYTES:
            data += b"\x00" * (FRAME_BYTES - len(data))
        return data

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._buffer.close()


def wav_to_playback(path: Path) -> bytes:
    """A WAV as 48 kHz stereo 16-bit (no ffmpeg needed)."""
    with wave.open(str(path), "rb") as wav:
        width, channels, rate = (
            wav.getsampwidth(),
            wav.getnchannels(),
            wav.getframerate(),
        )
        pcm = wav.readframes(wav.getnframes())
    if width != WIDTH:
        pcm = audioop.lin2lin(pcm, width, WIDTH)
    if channels == 2:
        pcm = audioop.tomono(pcm, WIDTH, 0.5, 0.5)
    elif channels > 2:
        raise ValueError(f"{channels}-channel audio is not supported")
    if rate != RATE:
        pcm, _ = audioop.ratecv(pcm, WIDTH, 1, rate, RATE, None)
    return audioop.tostereo(pcm, WIDTH, 1, 1)


async def decode_for_playback(path: Path) -> bytes:
    """Any audio file the Core hands over as 48 kHz stereo 16-bit PCM.

    A WAV is read directly; anything else goes through ffmpeg."""
    try:
        return await asyncio.to_thread(wav_to_playback, path)
    except (wave.Error, EOFError):
        pass
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            f"cannot play {path.name}: it is not a WAV and ffmpeg is missing"
        )
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "s16le",
        "-ar",
        str(RATE),
        "-ac",
        "2",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    if process.returncode != 0 or not out:
        raise RuntimeError(f"ffmpeg could not read {path.name}: {err.decode()[-200:]}")
    return out


def stream_to_playback(pcm: bytes, rate: int, state: Any = None) -> tuple[bytes, Any]:
    """A chunk of the model's voice (16-bit mono PCM at ``rate``) as 48 kHz stereo.

    ``state`` is the resampler's memory: pass back what the last chunk returned so the
    chunks join without a click."""
    pcm = pcm[: len(pcm) - len(pcm) % WIDTH]
    if rate != RATE:
        pcm, state = audioop.ratecv(pcm, WIDTH, 1, rate, RATE, state)
    return audioop.tostereo(pcm, WIDTH, 1, 1), state


def seconds_of(pcm: bytes) -> float:
    return len(pcm) / (RATE * WIDTH * 2)


# --------------------------------------------------------------------------- receiving

if HAS_VOICE_RECV and voice_recv is not None:

    class CallSink(voice_recv.AudioSink):  # type: ignore[misc]
        """What people say in the channel, as chunks for the Core.

        ``write`` runs on voice_recv's packet-router thread, so it only converts and hands
        the chunks to ``emit(speaker_id, speaker_name, pcm)``, which must be thread-safe.
        """

        def __init__(self, emit: Callable[[str, str, bytes], None]) -> None:
            super().__init__()
            self._emit = emit
            self._chunker = Chunker()

        def wants_opus(self) -> bool:
            return False  # let voice_recv decode (jitter buffer, FEC, loss concealment)

        def write(self, user: Any, data: Any) -> None:
            if user is None or getattr(user, "bot", False):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm or len(pcm) < 4:
                return
            speaker = str(getattr(user, "id", ""))
            name = str(
                getattr(user, "display_name", None) or getattr(user, "name", "") or ""
            )
            try:
                chunks = self._chunker.feed(speaker, pcm)
            except Exception:  # noqa: BLE001 - a bad packet must not end the call
                return
            for chunk in chunks:
                self._emit(speaker, name, chunk)

        def cleanup(self) -> None:
            self._chunker.clear()

else:  # pragma: no cover - without voice_recv the bot cannot listen
    CallSink = None  # type: ignore[assignment,misc]
