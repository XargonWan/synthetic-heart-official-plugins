"""Hello: the smallest useful extension.

It runs in its own process. It never imports the Core: everything it knows or does comes
from ``ctx``, and every call is checked against the capabilities in the manifest.
"""

from __future__ import annotations

import convert

from synth_sdk.runtime import Context


async def main(ctx: Context) -> None:
    starts = int(await ctx.storage.get("starts", 0)) + 1  # storage: your own key/value space
    await ctx.storage.set("starts", starts)

    greeting = await ctx.config.get("HELLO_GREETING")  # a setting declared in the manifest
    ctx.log.info(convert.greeting_line(greeting, starts))
    ctx.stats.gauge("starts", starts)  # shows up as "<your id>.starts" in Logs > Stats

    # Stay alive until the Core stops the extension. Never block the event loop in here:
    # every handler is async, and a slow call must be awaited.
    await ctx.stopping.wait()
