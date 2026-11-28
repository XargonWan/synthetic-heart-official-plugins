"""The real google-genai objects behind :mod:`engine`'s ``Wire`` and ``Connect``."""

from __future__ import annotations

from typing import Any

import convert


class _NoopAsyncHttpClient:
    async def aclose(self) -> None:
        return


class GenaiWire:
    """The SDK's request objects."""

    def __init__(self) -> None:
        from google.genai import types

        self._types = types

    def activity_start(self) -> Any:
        return self._types.ActivityStart()

    def activity_end(self) -> Any:
        return self._types.ActivityEnd()

    def blob(self, data: bytes, mime_type: str) -> Any:
        return self._types.Blob(data=data, mime_type=mime_type)

    def function_response(self, call_id: str, name: str, response: dict[str, Any]) -> Any:
        return self._types.FunctionResponse(id=call_id, name=name, response=response)


def connector(settings: convert.Settings) -> Any:
    """``(model, config) -> async context manager`` for one session."""
    from google import genai

    client = genai.Client(
        api_key=settings.api_key, http_options={"api_version": "v1alpha"}
    )
    # The SDK can raise an unhandled task exception at shutdown when the async HTTP client
    # was never created: give it a harmless one to close.
    api = getattr(client, "_api_client", None)
    if api is not None and getattr(api, "_async_httpx_client", None) is None:
        api._async_httpx_client = _NoopAsyncHttpClient()  # noqa: SLF001
    return lambda model, config: client.aio.live.connect(model=model, config=config)
