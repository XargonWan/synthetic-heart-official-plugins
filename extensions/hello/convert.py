"""Pure functions of the Hello extension: no I/O, so they are easy to unit test."""

from __future__ import annotations

DEFAULT_GREETING = "Hello from my first extension"


def greeting_line(greeting: object, starts: int) -> str:
    """The log line for the ``starts``-th start. An empty or non-text setting falls back to
    the default, because a setting is whatever the person typed."""
    text = greeting.strip() if isinstance(greeting, str) else ""
    return f"{text or DEFAULT_GREETING} (start number {starts})"
