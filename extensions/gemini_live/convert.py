"""Gemini Live: what the Core sends, as the Gemini Live API wants it.

Pure functions, no google-genai import, so they are tested without the library. The
configuration is built as plain dictionaries (the SDK accepts them) and the tool
declarations are rendered from the Core's neutral description.
"""

from __future__ import annotations

import base64
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

#: Gemini 3.1 Flash Live (audio to audio, low latency).
MODEL = "gemini-3.1-flash-live-preview"
#: Affective dialog and proactive audio are not supported by 3.1: this model has them.
LEGACY_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_VOICE = "Aoede"
INPUT_MIME = "audio/pcm;rate=16000"
OUTPUT_RATE = 24000
THINKING_LEVELS = ("minimal", "low", "medium", "high")

_TYPES = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}
_PASSTHROUGH = (
    "description",
    "enum",
    "format",
    "default",
    "nullable",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
)


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    affective: bool = False
    proactive: bool = False
    thinking_level: str = "minimal"
    thinking_budget: int = 0
    manual_vad: bool = True
    silence_seconds: float = 1.5


def settings_from(values: dict[str, Any]) -> Settings:
    """The extension's settings as typed values; a bad one falls back to its default."""

    def flag(key: str) -> bool:
        raw = values.get(key)
        return raw is True or (isinstance(raw, str) and raw.strip().lower() == "true")

    level = str(values.get("GEMINI_LIVE_THINKING_LEVEL") or "minimal").strip().lower()
    try:
        budget = max(0, int(values.get("GEMINI_LIVE_THINKING_BUDGET") or 0))
    except (TypeError, ValueError):
        budget = 0
    try:
        silence = float(values.get("GEMINI_LIVE_SILENCE_SECONDS") or 1.5)
    except (TypeError, ValueError):
        silence = 1.5
    return Settings(
        api_key=str(values.get("GEMINI_API_KEY") or "").strip(),
        affective=flag("GEMINI_LIVE_AFFECTIVE_DIALOG"),
        proactive=flag("GEMINI_LIVE_PROACTIVE_AUDIO"),
        thinking_level=level if level in THINKING_LEVELS else "minimal",
        thinking_budget=budget,
        manual_vad=str(values.get("GEMINI_LIVE_VAD") or "manual").strip().lower()
        != "server",
        silence_seconds=min(10.0, max(0.4, silence)),
    )


def choose_model(settings: Settings) -> str:
    return LEGACY_MODEL if settings.affective or settings.proactive else MODEL


# ----------------------------------------------------------------------- tools


def _schema(source: dict[str, Any]) -> dict[str, Any]:
    """One JSON schema with the type names Gemini wants (``STRING``, ``OBJECT``...)."""
    result: dict[str, Any] = {}
    kind = source.get("type")
    lowered = kind.lower() if isinstance(kind, str) else None
    if isinstance(kind, str):
        result["type"] = _TYPES.get(lowered or "", kind)
    for key in _PASSTHROUGH:
        if source.get(key) not in (None, ""):
            result[key] = deepcopy(source[key])
    items = source.get("items")
    if isinstance(items, dict):
        result["items"] = _schema(items)
    elif lowered == "array":
        result["items"] = {"type": "STRING"}  # Gemini refuses an array without items
    properties = source.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            str(name): _schema(field)
            for name, field in properties.items()
            if isinstance(field, dict)
        }
    elif lowered == "object":
        result["properties"] = {}
    required = source.get("required")
    if isinstance(required, list):
        result["required"] = [str(n) for n in required if isinstance(n, str)]
    for key in ("anyOf", "oneOf"):
        options = source.get(key)
        if isinstance(options, list):
            converted = [_schema(o) for o in options if isinstance(o, dict)]
            if converted:
                result[key] = converted
    return result


def tool_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """``[{"name", "description", "parameters": [{"name", "type", "description",
    "required", "enum", "schema"}]}]`` as one Gemini ``tools`` entry, or ``None``."""
    declarations: list[dict[str, Any]] = []
    for tool in tools or []:
        name = str(tool.get("name") or "")
        if not name:
            continue
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in tool.get("parameters") or []:
            pname = str(param.get("name") or "")
            if not pname:
                continue
            base = param.get("schema") or {
                "type": param.get("type") or "string",
                "description": param.get("description") or "",
                "enum": param.get("enum"),
            }
            properties[pname] = _schema(base)
            if param.get("required", True):
                required.append(pname)
        parameters: dict[str, Any] = {"type": "OBJECT", "properties": properties}
        if required:
            parameters["required"] = required
        declarations.append(
            {
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": parameters,
            }
        )
    return [{"function_declarations": declarations}] if declarations else None


# ---------------------------------------------------------------------- config


def build_config(
    *,
    settings: Settings,
    system_instruction: str,
    tools: list[dict[str, Any]],
    voice: str = "",
    resume_handle: str | None = None,
) -> dict[str, Any]:
    """The ``LiveConnectConfig`` of a session, as a dictionary."""
    model = choose_model(settings)
    config: dict[str, Any] = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": voice.strip() or DEFAULT_VOICE}
            }
        },
        "system_instruction": system_instruction,
        # Lets a 3.1 session be seeded with an initial history (it is otherwise
        # restricted); harmless on 2.5.
        "history_config": {"initial_history_in_client_content": True},
        # A sliding window keeps an audio session alive past the raw 15 minutes.
        "context_window_compression": {"sliding_window": {}},
        # The server sends resumable handles; a handle passed here resumes that context.
        "session_resumption": {"handle": resume_handle},
    }
    if settings.manual_vad:
        # A voice channel stops sending packets while its user is silent, which freezes
        # the server's detector; the extension sends activity_start / activity_end.
        config["realtime_input_config"] = {
            "automatic_activity_detection": {"disabled": True}
        }
    if settings.affective:
        config["enable_affective_dialog"] = True
    if settings.proactive:
        config["proactivity"] = {"proactive_audio": True}
    if model == MODEL:
        config["thinking_config"] = {
            "thinking_level": settings.thinking_level.upper(),
            "include_thoughts": False,
        }
    elif settings.thinking_budget > 0:
        config["thinking_config"] = {
            "thinking_budget": settings.thinking_budget,
            "include_thoughts": False,
        }
    declared = tool_declarations(tools)
    if declared:
        config["tools"] = declared
    return config


# -------------------------------------------------------------------- messages


def extract_payload(message: Any) -> tuple[bytes | None, str | None]:
    """Audio and text carried by one server message.

    Gemini 3.1 may put several parts in ``server_content.model_turn.parts``; the SDK's
    ``message.data`` / ``message.text`` shortcuts are only a fallback."""
    audio: list[bytes] = []
    text: list[str] = []
    content = getattr(message, "server_content", None)
    turn = getattr(content, "model_turn", None) if content else None
    for part in getattr(turn, "parts", None) or []:
        inline = getattr(part, "inline_data", None)
        if inline is not None:
            mime = str(getattr(inline, "mime_type", "") or "").lower()
            data = getattr(inline, "data", None)
            if data and (not mime or mime.startswith("audio/")):
                if isinstance(data, str):
                    try:
                        data = base64.b64decode(data)
                    except ValueError:
                        data = None
                if isinstance(data, bytes):
                    audio.append(data)
        if getattr(part, "text", None):
            text.append(str(part.text))
    fallback = getattr(message, "data", None)
    if not audio and isinstance(fallback, bytes) and fallback:
        audio.append(fallback)
    if not text and getattr(message, "text", None):
        text.append(str(message.text))
    return (b"".join(audio) or None, "".join(text).strip() or None)


def clean_transcript(parts: list[str]) -> str:
    """Fragments joined as Gemini streams them (with their own spaces), tidied."""
    return re.sub(r" {2,}", " ", "".join(parts)).strip()


# ------------------------------------------------------------------------ kicks


def kick_text(*, reconnect: bool = False, history: str = "") -> str:
    """The first text turn of a session that has no context to continue."""
    if reconnect:
        return (
            "[Voice session refreshed - you are continuing the same conversation. "
            "Do NOT greet or introduce yourself again. Simply acknowledge briefly "
            f"that you're back and continue naturally.{history}]"
        )
    return "[Session started. Greet naturally.]"


def history_snippet(lines: list[tuple[str, str]], limit: int = 20) -> str:
    """The last exchanges, for a session that cannot be resumed."""
    kept = [f"{who}: {text}" for who, text in lines[-limit:] if text.strip()]
    return "\n\nRecent conversation context:\n" + "\n".join(kept) if kept else ""
