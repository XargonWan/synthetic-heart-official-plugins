"""The Hello extension, run for real in its own process behind the Core's gateway.

This is the pattern to copy: a *real* extension process, the Core's *real* gateway and
permission checks, and in-memory stand-ins for what the Core would store.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from core.extensions.api import register_core_api
from core.extensions.gateway import Gateway
from core.extensions.grants import MemoryGrantStore
from core.extensions.launcher import Launcher
from core.extensions.manifest import load_manifest
from core.extensions.memory_ports import MemoryConfig, memory_ports
from core.extensions.sandbox import RuntimeLayout, SandboxPolicy
from core.extensions.supervisor import RestartPolicy, Supervisor
from tests.support.waiting import wait_until

SOURCE = Path(__file__).resolve().parent.parent / "extensions" / "hello"
MANIFEST = load_manifest(SOURCE / "synth-extension.toml")
EXT_ID = MANIFEST.id


@pytest_asyncio.fixture
async def rig(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    ports, grants = memory_ports(), MemoryGrantStore()
    gateway = Gateway(grants=grants)
    register_core_api(gateway, ports)
    layout = RuntimeLayout(tmp_path / "extensions")
    launcher = Launcher(gateway, layout, SandboxPolicy(drop_privileges=False))
    supervisor = Supervisor(
        launcher,
        RestartPolicy(
            initial_delay=0.05,
            max_delay=0.1,
            max_failures=3,
            hello_timeout=10.0,
            stop_grace=1.0,
            term_wait=0.5,
        ),
    )
    try:
        yield SimpleNamespace(
            layout=layout, grants=grants, ports=ports, supervisor=supervisor
        )
    finally:
        await supervisor.stop_all()
        await gateway.close_all()


async def start(rig: SimpleNamespace, **config: object) -> None:
    """Install the extension from its source folder, as the installer would, and start it."""
    directory = rig.layout.install_dir(EXT_ID)
    if not directory.exists():
        directory.mkdir(parents=True)
        for item in SOURCE.iterdir():
            if item.is_file():
                shutil.copy(item, directory / item.name)
    rig.grants.grant(EXT_ID, *MANIFEST.permissions.capabilities)
    if config:
        port = rig.ports.config
        assert isinstance(port, MemoryConfig)
        port.values[EXT_ID] = dict(config)
    rig.supervisor.register(MANIFEST)
    await rig.supervisor.start(EXT_ID)
    await wait_until(
        lambda: rig.supervisor.status()[EXT_ID]["state"] == "running", 15
    )
    await asyncio.sleep(0.3)  # main() has made its calls


def stored(rig: SimpleNamespace, key: str) -> object:
    text = rig.ports.storage._data.get(EXT_ID, {}).get(key)  # type: ignore[attr-defined]  # noqa: SLF001
    return json.loads(text) if text is not None else None


@pytest.mark.asyncio
async def test_it_starts_and_counts_its_start(rig: SimpleNamespace) -> None:
    await start(rig)
    assert rig.supervisor.status()[EXT_ID]["state"] == "running"
    assert stored(rig, "starts") == 1


@pytest.mark.asyncio
async def test_a_restart_counts_again(rig: SimpleNamespace) -> None:
    await start(rig)
    await rig.supervisor.stop(EXT_ID)
    await rig.supervisor.start(EXT_ID)
    await wait_until(
        lambda: rig.supervisor.status()[EXT_ID]["state"] == "running", 15
    )
    await asyncio.sleep(0.3)
    assert stored(rig, "starts") == 2


@pytest.mark.asyncio
async def test_without_the_grant_it_cannot_store(rig: SimpleNamespace) -> None:
    """The permission check is the Core's: declaring a capability is not enough."""
    directory = rig.layout.install_dir(EXT_ID)
    directory.mkdir(parents=True)
    for item in SOURCE.iterdir():
        if item.is_file():
            shutil.copy(item, directory / item.name)
    rig.grants.grant(EXT_ID, "log", "config")  # no "storage"
    rig.supervisor.register(MANIFEST)
    await rig.supervisor.start(EXT_ID)
    await asyncio.sleep(1.0)
    assert stored(rig, "starts") is None
