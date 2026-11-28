"""Gemini Live: the model connection of a real-time voice conversation.

The Core owns the conversation (persona, tools, history, the call); this extension turns
its neutral session into a Gemini Live socket and reports what the model does. See
``engine.py``.
"""

from __future__ import annotations

import convert
import engine
import wire

from synth_sdk.runtime import Context

NAME = "gemini_live"
KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_LIVE_AFFECTIVE_DIALOG",
    "GEMINI_LIVE_PROACTIVE_AUDIO",
    "GEMINI_LIVE_THINKING_LEVEL",
    "GEMINI_LIVE_THINKING_BUDGET",
    "GEMINI_LIVE_VAD",
    "GEMINI_LIVE_SILENCE_SECONDS",
)


async def main(ctx: Context) -> None:
    async def load() -> convert.Settings:
        return convert.settings_from(
            {key: value for key in KEYS if (value := await ctx.config.get(key)) is not None}
        )

    gemini = engine.GeminiLiveEngine(
        load,
        lambda settings: wire.connector(settings),
        wire.GenaiWire(),
        ctx.log,
    )
    await ctx.live.serve(NAME, gemini)
    ctx.log.info(f"offering the {NAME} Live engine")
    await ctx.stopping.wait()
