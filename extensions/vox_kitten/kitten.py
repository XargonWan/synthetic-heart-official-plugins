"""KittenTTS: local, neural, multi-voice text-to-speech (ONNX, CPU).

The model is fetched from HuggingFace on first use into the extension's data directory and
kept in memory per model id. The inference code is KittenTTS's own, vendored in
``kittentts_core`` (onnxruntime + espeak-ng; no spaCy, no PyTorch: see its NOTICE). Voices are language-agnostic: the model adapts its phonetics
to the text, and the espeak-ng phonemizer is swapped per call so non-English text is not
read with English rules (kittentts 0.8.x hardcodes ``en-us`` inside its constructor).
"""

from __future__ import annotations

import io
import json
import threading
import wave
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "kitten-tts-mini-0.8"
DEFAULT_VOICE = "Bella"
DEFAULT_SPEED = 1.3
SAMPLE_RATE = 24000

#: The models the engine offers: id -> (HuggingFace repo, label, approximate size in MB).
MODELS: dict[str, tuple[str, str, int]] = {
    "kitten-tts-mini-0.8": (
        "KittenML/kitten-tts-mini-0.8",
        "KittenTTS Mini 0.8 (default): higher-quality multi-voice model, ONNX, CPU",
        80,
    ),
    "kitten-tts-nano-0.8": (
        "KittenML/kitten-tts-nano-0.8",
        "KittenTTS Nano 0.8: compact multi-voice model, ONNX, CPU",
        25,
    ),
    "kitten-tts-micro-0.8": (
        "KittenML/kitten-tts-micro-0.8",
        "KittenTTS Micro 0.8: ultra-compact multi-voice model, ONNX, CPU",
        15,
    ),
}

# The model card's canonical voice names; a voice is language-agnostic.
VOICES: tuple[tuple[str, str], ...] = (
    ("Bella", "F"),
    ("Jasper", "M"),
    ("Luna", "F"),
    ("Bruno", "M"),
    ("Rosie", "F"),
    ("Hugo", "M"),
    ("Kiki", "F"),
    ("Leo", "M"),
)

# ISO 639-1 -> espeak-ng language identifier, to replace kittentts' hardcoded ``en-us``.
ESPEAK_LANG: dict[str, str] = {
    "en": "en-us",
    "it": "it",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "pt": "pt",
    "nl": "nl",
    "pl": "pl",
    "ru": "ru",
    "ko": "ko",
    "ar": "ar",
    # Japanese: espeak-ng 'ja' is not reliably present and kittentts was trained on
    # Latin-script phonemes; Italian shares its open vowels and clear consonants, so it is
    # more intelligible than the English fallback.
    "ja": "it",
    "jp": "it",
}


HF_BASE = "https://huggingface.co"
#: How long one file download may take (the models are 15 to 80 MB).
DOWNLOAD_TIMEOUT_SEC = 600.0


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


def espeak_lang_code(lang: str | None) -> str:
    """The espeak-ng code for an ISO 639-1 tag (``it`` and ``it-IT`` alike); ``en-us`` if unknown."""
    if not lang:
        return "en-us"
    return ESPEAK_LANG.get(lang.split("-")[0].lower(), "en-us")


def download(url: str, target: Path) -> None:
    """Fetch ``url`` into ``target`` (atomically: a partial file is never left in place)."""
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SEC
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def fetch_model(repo: str, directory: Path) -> tuple[Path, Path, dict[str, Any]]:
    """The model file, the voices file and the config of a HuggingFace repo, downloaded
    into ``directory`` when they are not there yet."""
    folder = directory / repo.replace("/", "__")
    config_path = folder / "config.json"
    if not config_path.is_file():
        Log.info(f"[vox/kitten] downloading the model {repo!r}")
        download(f"{HF_BASE}/{repo}/resolve/main/config.json", config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("type") not in ("ONNX1", "ONNX2"):
        raise ValueError(f"unsupported model type {config.get('type')!r}")
    files: list[Path] = []
    for name in (config["model_file"], config["voices"]):
        path = folder / name
        if not path.is_file():
            download(f"{HF_BASE}/{repo}/resolve/main/{name}", path)
        files.append(path)
    return files[0], files[1], config


class KittenEngine:
    """One KittenTTS model, loaded from the data directory (downloaded on first use)."""

    def __init__(self, repo: str, directory: Path | None = None) -> None:
        from kittentts_core.onnx_model import KittenTTS_1_Onnx

        model_path, voices_path, config = fetch_model(repo, directory or Paths.models)
        self.model = KittenTTS_1_Onnx(
            model_path=str(model_path),
            voices_path=str(voices_path),
            speed_priors=config.get("speed_priors", {}),
            voice_aliases=config.get("voice_aliases", {}),
        )

    def generate(self, text: str, voice: str, speed: float) -> Any:
        return self.model.generate(text, voice=voice, speed=speed, clean_text=False)


class LocalKittenTTS:
    """One loaded model, with the phonemizer swapped per call (thread-safe)."""

    def __init__(self, repo: str, factory: Any) -> None:
        self._engine = factory(repo)
        self._lock = threading.Lock()
        self._phonemizers: dict[str, Any] = {}

    def _phonemizer(self, espeak_lang: str) -> Any:
        if espeak_lang not in self._phonemizers:
            from phonemizer.backend import EspeakBackend  # ty: ignore[unresolved-import]

            try:
                self._phonemizers[espeak_lang] = EspeakBackend(
                    language=espeak_lang, preserve_punctuation=True, with_stress=True
                )
            except Exception as exc:  # noqa: BLE001
                Log.error(
                    f"[vox/kitten] no espeak backend for {espeak_lang!r}: {exc}; using en-us"
                )
                if "en-us" not in self._phonemizers:
                    self._phonemizers["en-us"] = EspeakBackend(
                        language="en-us", preserve_punctuation=True, with_stress=True
                    )
                self._phonemizers[espeak_lang] = self._phonemizers["en-us"]
        return self._phonemizers[espeak_lang]

    def generate(
        self, text: str, voice: str, language: str | None, speed: float
    ) -> Any:
        """The audio of ``text`` (a numpy array). The phonemizer is swapped for the language
        and restored; a lock serialises the swap so concurrent calls do not race."""
        espeak_lang = espeak_lang_code(language)
        Log.info(
            f"[vox/kitten] generate: voice={voice!r} lang={language!r} espeak={espeak_lang!r} speed={speed}"
        )
        model = self._engine.model
        with self._lock:
            previous = model.phonemizer
            model.phonemizer = self._phonemizer(espeak_lang)
            try:
                return self._engine.generate(text=text, voice=voice, speed=speed)
            finally:
                model.phonemizer = previous


_models: dict[str, LocalKittenTTS] = {}
_models_lock = threading.Lock()


def load_model(model_id: str, factory: Any = None) -> LocalKittenTTS:
    """The cached engine for a model id (a catalogue id or a raw HuggingFace repo / folder)."""
    with _models_lock:
        if model_id in _models:
            return _models[model_id]
    repo = MODELS[model_id][0] if model_id in MODELS else model_id
    if factory is None:
        factory = KittenEngine
    instance = LocalKittenTTS(repo, factory)
    with _models_lock:
        _models[model_id] = instance
    return instance


def to_wav(audio: Any, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A float audio array as 16-bit mono WAV bytes, peak-normalised to -1 dBFS.

    Clean peak normalisation only: no makeup gain and no hard clipping, because transparent
    loudness is handled downstream (ffmpeg ``dynaudnorm`` when broadcasting).
    """
    import numpy as np

    arr = audio.numpy() if hasattr(audio, "numpy") else audio
    arr = np.asarray(arr, dtype=np.float64)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 1e-10:
        arr = arr / peak * 0.891
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def parse_language_models(raw: Any) -> dict[str, str]:
    """``{"it": "kitten-tts-nano-it-0.1"}`` from the setting's JSON text; ``{}`` if unusable."""
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def speakers() -> list[dict[str, str]]:
    return [
        {"reference_id": name, "title": name, "language": "*", "gender": gender}
        for name, gender in VOICES
    ]


def model_infos() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "name": label,
            "owned_by": "KittenML",
            "capabilities": {"vox": True},
            "model_type": "tts",
            "input_modalities": ["text"],
            "output_modalities": ["audio"],
        }
        for model_id, (_, label, _size) in MODELS.items()
    ]


def synthesize(
    text: str,
    voice: str | None,
    kwargs: dict[str, Any],
    *,
    default_model: str,
    language_models: dict[str, str],
    speed: float,
    factory: Any = None,
) -> bytes | None:
    """The text as WAV bytes (blocking: run it in a thread), or ``None`` on failure."""
    language = kwargs.get("language") or None
    if language and language in language_models:
        model_id = language_models[language]
        Log.info(f"[vox/kitten] using the model {model_id!r} for language {language!r}")
    else:
        model_id = str(kwargs.get("model") or kwargs.get("model_id") or default_model)
    chosen_voice = str(voice or kwargs.get("speaker") or DEFAULT_VOICE)
    try:
        chosen_speed = float(kwargs.get("speed") or speed)
    except (TypeError, ValueError):
        chosen_speed = speed
    try:
        engine = load_model(model_id, factory)
        return to_wav(engine.generate(text, chosen_voice, language, chosen_speed))
    except Exception as exc:  # noqa: BLE001 - the caller falls back to text
        Log.error(f"[vox/kitten] TTS generation failed: {exc}")
        return None
