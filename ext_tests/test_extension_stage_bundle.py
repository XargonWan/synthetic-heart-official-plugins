"""The official Stage package and the default list of the image, against the real sources."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from core.extensions.memory_ports import memory_ports
from core.extensions.repo.installer import Installer
from core.extensions.runtime import ExtensionRuntime
from core.extensions.sandbox import SandboxPolicy
from core.extensions.supervisor import RestartPolicy
from core.extensions.webui_web import build_router
from ext_tests import CORE, ROOT

FAST = RestartPolicy(
    initial_delay=0.05,
    max_failures=2,
    hello_timeout=10.0,
    stop_grace=0.5,
    term_wait=0.3,
)


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_bundled_extensions", CORE / "scripts" / "build_bundled_extensions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_bundled_extensions"] = module
    spec.loader.exec_module(module)
    return module


def test_the_real_defaults_all_have_a_source() -> None:
    script = load_script()
    for extension_id in script.DEFAULTS:
        assert (
            script.source_of(extension_id, ROOT / "extensions") / "synth-extension.toml"
        ).is_file(), extension_id


REAL_BUILD = ROOT / "extensions" / "synth_stage" / "web" / "index.html"


@pytest.mark.skipif(
    not REAL_BUILD.is_file(),
    reason="the Stage client is not built (scripts/build_stage_extension.sh)",
)
@pytest.mark.asyncio
async def test_the_real_stage_build_is_bundled_and_served(tmp_path: Path) -> None:
    """With the real client built, its files are what gets packed and served, and its
    own asset paths (``/stage/assets/...``) resolve through the alias."""
    import re

    script = load_script()
    bundled = script.build(
        tmp_path / "bundled", ("official/synth_stage",), sources=ROOT / "extensions"
    )
    root = tmp_path / "extensions"
    installer = Installer(root, bundled=bundled)
    assert installer.ensure_defaults() == (["official/synth_stage"], [])
    runtime = ExtensionRuntime(
        root,
        memory_ports(),
        policy=SandboxPolicy(drop_privileges=False),
        restart=FAST,
    )
    app = FastAPI()
    app.include_router(build_router(runtime.gateway, runtime.layout.install_dir))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as http:
            await runtime.start()
            index = await http.get("/stage/")
            asset_path = re.search(r'src="(/stage/assets/[^"]+\.js)"', index.text)
            assert asset_path is not None, index.text[:300]
            asset = await http.get(asset_path.group(1))
    finally:
        await runtime.stop()
    assert index.status_code == 200 and "<div id=" in index.text
    assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
    # Stage declared same_origin and was granted web.origin: no sandbox policy.
    assert "content-security-policy" not in index.headers
