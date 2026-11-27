"""Vosk hearing engine extension.

Local speech recognition as an endpoint protocol: the Core's hearing pipeline sees an endpoint
called ``vosk`` (created here) and asks this extension to transcribe. Talks to the Core only
through ``ctx``; imports nothing from the Core.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import vosk_stt
from synth_sdk.runtime import Context

ENDPOINT = "vosk"


async def main(ctx: Context) -> None:
    vosk_stt.Log.target = ctx.log
    vosk_stt.Paths.models = ctx.data_dir / "models"

    async def handle(
        protocol: str,
        op: str,
        endpoint: dict[str, Any],
        api_key: str,
        args: dict[str, Any],
    ) -> Any:
        if op == "models":
            return vosk_stt.model_infos()
        if op == "capabilities":
            return {"cortex": False, "vox": False, "auris": True, "live": False}
        if op == "health":
            return True
        if op == "ping":
            return {"ok": False, "text": "Vosk is a hearing engine (no chat ping)"}
        if op == "stt":
            values = await ctx.config.all()
            kwargs = dict(args.get("kwargs") or {})
            text = await asyncio.to_thread(
                vosk_stt.transcribe,
                Path(str(args["path"])),
                language=str(
                    kwargs.get("language") or values.get("VOSK_LANGUAGE") or ""
                ),
                explicit_model=str(
                    kwargs.get("model") or values.get("VOSK_MODEL") or ""
                ),
            )
            return {"text": text or ""}
        raise ValueError(f"operation {op!r} is not provided by this protocol")

    ctx.protocols.on_request(handle)
    await ctx.endpoints.ensure(
        ENDPOINT,
        "",
        display_label="Vosk",
        protocol="vosk",
        extra_config={},
        subsystem_map={"auris": True},
    )
    await ctx.stopping.wait()
