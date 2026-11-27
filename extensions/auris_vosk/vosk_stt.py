"""Vosk speech recognition (offline, CPU): model download, audio conversion, transcription.

A model is the small one of the configured language (a few dozen MB, fetched from
alphacephei.com on first use into the extension's data directory) unless a model id is set.
Any recording is converted to 16 kHz mono PCM with ``ffmpeg`` first; a recording that already
is one is read as it is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import wave
import zipfile
from pathlib import Path
from typing import Any

DEFAULT_LANGUAGE = "en"
MODELS_URL = "https://alphacephei.com/vosk/models"
DOWNLOAD_TIMEOUT_SEC = 1800.0
CONVERT_TIMEOUT_SEC = 60

#: id -> (label, language, size in MB, archive name)
MODELS: dict[str, tuple[str, str, int, str]] = {
    "vosk-en-us": ("Vosk English (US), small", "en", 50, "vosk-model-small-en-us-0.15"),
    "vosk-en-us-large": (
        "Vosk English (US), large",
        "en",
        1800,
        "vosk-model-en-us-0.42-gigaspeech",
    ),
    "vosk-it-it": ("Vosk Italiano, small", "it", 50, "vosk-model-small-it-0.22"),
    "vosk-fr-fr": ("Vosk Français, small", "fr", 50, "vosk-model-small-fr-0.22"),
    "vosk-es-es": ("Vosk Español, small", "es", 50, "vosk-model-small-es-0.42"),
    "vosk-de-de": ("Vosk Deutsch, small", "de", 50, "vosk-model-small-de-0.15"),
    "vosk-pt-pt": ("Vosk Português, small", "pt", 50, "vosk-model-small-pt-0.3"),
    "vosk-zh-cn": ("Vosk 中文, small", "zh", 50, "vosk-model-small-cn-0.22"),
    "vosk-ja-jp": ("Vosk 日本語, small", "ja", 50, "vosk-model-small-ja-0.22"),
    "vosk-ko-kr": ("Vosk 한국어, small", "ko", 50, "vosk-model-small-ko-0.22"),
}

#: language tag -> the small model of that language
LANGUAGE_MODEL: dict[str, str] = {
    "en": "vosk-en-us",
    "it": "vosk-it-it",
    "fr": "vosk-fr-fr",
    "es": "vosk-es-es",
    "de": "vosk-de-de",
    "pt": "vosk-pt-pt",
    "zh": "vosk-zh-cn",
    "ja": "vosk-ja-jp",
    "ko": "vosk-ko-kr",
}


class Paths:
    """Where the models are kept; ``ext_main`` points it at the extension's data directory."""

    models: Path = Path("models")


class Log:
    """Where this module reports; ``ext_main`` points it at ``ctx.log``."""

    target: Any = None

    @classmethod
    def info(cls, message: str) -> None:
        if cls.target is not None:
            cls.target.info(message)

    @classmethod
    def error(cls, message: str) -> None:
        if cls.target is not None:
            cls.target.error(message)


def model_for(language: str | None, explicit: str | None = None) -> str:
    """The model id to use: the explicit one, else the small model of the language
    (``it``, ``it-IT`` and ``IT`` alike; ``auto`` or unknown means English)."""
    if explicit and explicit in MODELS:
        return explicit
    tag = str(language or "").split("-")[0].split("_")[0].lower()
    return LANGUAGE_MODEL.get(tag, LANGUAGE_MODEL[DEFAULT_LANGUAGE])


def model_infos() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "name": label,
            "owned_by": "alphacephei",
            "capabilities": {"auris": True},
            "model_type": "stt",
            "input_modalities": ["audio"],
            "output_modalities": ["text"],
            "languages": [{"code": language, "name": language}],
        }
        for model_id, (label, language, _size, _name) in MODELS.items()
    ]


def _is_model_dir(path: Path) -> bool:
    return (path / "am").is_dir() or (path / "conf").is_dir()


def _download(url: str, target: Path) -> None:
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SEC
    ) as response:
        response.raise_for_status()
        with target.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)


def _extract(archive: Path, destination: Path) -> None:
    """Unpack a model archive into ``destination`` (its single top folder is dropped)."""
    with zipfile.ZipFile(archive) as zf:
        root = destination.resolve()
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        prefix = names[0].split("/", 1)[0] + "/" if names and "/" in names[0] else ""
        for info in zf.infolist():
            relative = info.filename[len(prefix) :] if prefix else info.filename
            if not relative or info.is_dir():
                continue
            target = (destination / relative).resolve()
            if root not in target.parents:  # a path that climbs out of the folder
                raise ValueError(f"unsafe path in the model archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


_download_lock = threading.Lock()


def ensure_model(model_id: str, directory: Path | None = None) -> Path:
    """The folder of a model, downloaded and unpacked first when it is not there yet."""
    base = directory or Paths.models
    target = base / model_id
    with _download_lock:
        if _is_model_dir(target):
            return target
        _, _, _, archive_name = MODELS[model_id]
        Log.info(f"[auris/vosk] downloading the model {model_id!r}")
        archive = base / f"{model_id}.zip.part"
        staging = base / f"{model_id}.staging"
        try:
            shutil.rmtree(staging, ignore_errors=True)
            _download(f"{MODELS_URL}/{archive_name}.zip", archive)
            _extract(archive, staging)
            if not _is_model_dir(staging):
                raise ValueError("the downloaded archive is not a Vosk model")
            shutil.rmtree(target, ignore_errors=True)
            staging.replace(target)
        finally:
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
    return target


_loaded: dict[str, Any] = {}
_loaded_lock = threading.Lock()


def load_model(
    model_id: str, directory: Path | None = None, factory: Any = None
) -> Any:
    """The loaded ``vosk.Model`` of ``model_id`` (kept in memory once loaded)."""
    with _loaded_lock:
        if model_id in _loaded:
            return _loaded[model_id]
    path = ensure_model(model_id, directory)
    if factory is None:
        import vosk

        vosk.SetLogLevel(-1)
        factory = vosk.Model
    model = factory(str(path))
    with _loaded_lock:
        _loaded[model_id] = model
    return model


def to_wav_16k(source: Path) -> tuple[Path, bool]:
    """``(path, converted)``: the recording as 16 kHz mono PCM. A WAV that already is one is
    returned as it is; anything else goes through ``ffmpeg`` (the caller deletes a converted
    file)."""
    try:
        with wave.open(str(source), "rb") as wf:
            if wf.getframerate() == 16000 and wf.getnchannels() == 1:
                return source, False
    except (wave.Error, EOFError, OSError):
        pass
    handle, name = tempfile.mkstemp(suffix=".wav", prefix="vosk_", dir=source.parent)
    os.close(handle)
    converted = Path(name)
    try:
        done = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                name,
            ],
            capture_output=True,
            timeout=CONVERT_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        converted.unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg is not installed: it is needed to read the recording"
        )
    if done.returncode != 0:
        converted.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg could not read the recording: {done.stderr.decode(errors='replace')[:200]}"
        )
    return converted, True


def transcribe(
    audio: Path,
    *,
    language: str | None,
    explicit_model: str | None = None,
    directory: Path | None = None,
    factory: Any = None,
    recognizer: Any = None,
) -> str | None:
    """What was said in the recording (blocking: run it in a thread), or ``None`` when
    nothing was recognised."""
    model_id = model_for(language, explicit_model)
    try:
        model = load_model(model_id, directory, factory)
        wav, converted = to_wav_16k(audio)
    except Exception as exc:  # noqa: BLE001 - reported, the caller hears nothing
        Log.error(f"[auris/vosk] cannot prepare the transcription: {exc}")
        return None
    try:
        if recognizer is None:
            import vosk

            recognizer = vosk.KaldiRecognizer
        with wave.open(str(wav), "rb") as wf:
            rec = recognizer(model, wf.getframerate())
            rec.SetWords(True)
            parts: list[str] = []
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    text = str(json.loads(rec.Result()).get("text", "")).strip()
                    if text:
                        parts.append(text)
            text = str(json.loads(rec.FinalResult()).get("text", "")).strip()
            if text:
                parts.append(text)
    except Exception as exc:  # noqa: BLE001
        Log.error(f"[auris/vosk] transcription failed: {exc}")
        return None
    finally:
        if converted:
            wav.unlink(missing_ok=True)
    result = " ".join(parts).strip()
    Log.info(f"[auris/vosk] transcribed {len(result)} characters ({model_id})")
    return result or None
