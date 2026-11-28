"""Pure helpers for the Discord extension (no network, no Core, no discord.py).

Standard library only, so they are unit tested without a Discord connection.
"""

from __future__ import annotations

import re
from typing import Any

INTERFACE_ID = "discord_bot"

#: Discord refuses a message longer than this many characters.
MESSAGE_LIMIT = 2000
#: Files larger than this are not downloaded.
MAX_DOWNLOAD = 25 * 1024 * 1024


def build_path(
    guild_id: Any, channel_id: Any, thread_id: Any = None, user_id: Any = None
) -> str:
    """The Interface Path of a conversation.

    ``discord_bot/<guild>/<channel>`` in a server channel,
    ``discord_bot/<guild>/<thread>/<thread>`` in a thread (a thread is a channel of its
    own on Discord, and this is how its history has always been stored: the id twice),
    ``discord_bot/<user>`` in a direct message.
    """
    if guild_id is None:
        return f"{INTERFACE_ID}/{user_id if user_id is not None else channel_id}"
    if thread_id is not None:
        return f"{INTERFACE_ID}/{guild_id}/{channel_id}/{thread_id}"
    return f"{INTERFACE_ID}/{guild_id}/{channel_id}"


def levels_of(path: str | None) -> list[str] | None:
    """The levels after ``discord_bot/`` (1 to 3 numeric ids), or ``None``."""
    if not path or not isinstance(path, str):
        return None
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != INTERFACE_ID or not 2 <= len(parts) <= 4:
        return None
    levels = parts[1:]
    return levels if all(level.isdigit() for level in levels) else None


def destination_of(path: str | None) -> str | None:
    """Where a message to this path goes: the last level (a thread inside a channel, the
    channel of a server, or a user for a direct message). A guild is never a destination."""
    levels = levels_of(path)
    return levels[-1] if levels else None


def is_direct(path: str | None) -> bool:
    """``discord_bot/<user>``: a conversation with one person."""
    levels = levels_of(path)
    return levels is not None and len(levels) == 1


def usable_reply_id(reply_to: Any, channel: str) -> int | None:
    """A message id to quote, or ``None``: it must be numeric and is never the channel."""
    text = str(reply_to).strip() if reply_to is not None else ""
    return int(text) if text.isdigit() and text != str(channel) else None


def split_text(text: str | None, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Chunks of at most ``limit`` characters.

    Prefers a paragraph, then a line, then a sentence end, and falls back to a hard cut
    when a run of text has no boundary inside the window. ``[]`` for empty text.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
        if cut < limit // 3:
            cut = limit  # no usable boundary: hard cut rather than send too much
        else:
            cut += 1 if window[cut] == "\n" else 2
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


_USER_MENTION = re.compile(r"<@!?(\d+)>")


def spell_mentions(
    content: str,
    *,
    bot_id: int | str | None,
    bot_name: str,
    roles: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """``content`` with the bot's mention written as ``@name`` and each role mention as
    ``@role``; and whether the bot itself was mentioned (the only mention that wakes it).
    Other people's mentions stay as Discord wrote them."""
    mentioned = False
    if bot_id is not None:

        def swap(match: re.Match[str]) -> str:
            nonlocal mentioned
            if match.group(1) == str(bot_id):
                mentioned = True
                return f"@{bot_name}"
            return match.group(0)

        content = _USER_MENTION.sub(swap, content)
    for role_id, role_name in (roles or {}).items():
        content = content.replace(f"<@&{role_id}>", f"@{role_name}")
    return content, mentioned


def is_ping(content: str, bot_name: str) -> bool:
    """``ping`` (with or without the bot's mention): answered with ``pong`` right here."""
    stripped = (
        content.replace(f"@{bot_name}", "").strip() if bot_name else content.strip()
    )
    return stripped.lower() == "ping"


def is_explicit(*, direct: bool, mentioned: bool, replied_to_bot: bool) -> bool:
    """Whether the bot was addressed: a direct message, its own mention, or a reply to
    one of its messages. A plain ``@`` in the text is not enough."""
    return direct or mentioned or replied_to_bot


def reply_block(
    *,
    message_id: Any,
    text: str | None,
    author_id: Any,
    author_name: str | None,
) -> dict[str, Any]:
    """What the person replied to, in the shape the Core takes."""
    info: dict[str, Any] = {
        "sender_name": author_name or "Unknown",
        "text": text or "",
        "message_id": str(message_id),
    }
    if author_id is not None:
        info["sender_id"] = str(author_id)
    return info


def audio_kind(content_type: str | None, filename: str | None) -> bool:
    """Whether an attachment is sound (a voice message or an audio file)."""
    if content_type and content_type.lower().startswith("audio/"):
        return True
    return bool(filename) and str(filename).lower().endswith(
        (".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".flac")
    )


#: Sticker pictures larger than this are not downloaded.
MAX_STICKER_BYTES = 2 * 1024 * 1024


def sticker_note(name: str, kind: str, *, downloaded: bool = False) -> str:
    """What to tell the model about a sticker it cannot be given as a picture."""
    if kind == "png":  # a static sticker that could not be fetched
        return f"[A sticker named '{name}' was sent, but it could not be downloaded.]"
    return (
        f"[A sticker named '{name}' was sent, but its format ({kind or 'unknown'}) "
        "cannot be shown as a still image.]"
    )


def sticker_filename(sticker_id: Any, name: str) -> str:
    """A safe file name for a downloaded sticker."""
    plain = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    return f"sticker_{sticker_id}_{plain or 'sticker'}.png"
