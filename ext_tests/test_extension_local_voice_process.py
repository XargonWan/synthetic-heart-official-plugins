"""The two local voice extensions as real processes with their real libraries and models.

Slow and online: the libraries are built from the local ``uv`` cache and the models are
downloaded (about 30 MB and 50 MB). Set ``SYNTH_TEST_NETWORK=1`` to run them. KittenTTS
speaks a sentence, and Vosk, in the other extension, hears what was said.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.extensions.api import register_core_api
from core.extensions.endpoints import register_endpoint_api
from core.extensions.gateway import Gateway
from core.extensions.grants import MemoryGrantStore
from core.extensions.interfaces import InterfacePorts, register_interface_api
from core.extensions.launcher import Launcher
from core.extensions.manifest import load_manifest
from core.extensions.media import MediaBridge
from core.extensions.memory_ports import (
    MemoryInterfaceHost,
    MemoryMessaging,
    memory_ports,
)
from core.extensions.protocols import attach_protocols
from core.extensions.sandbox import RuntimeLayout, SandboxPolicy
from core.extensions.supervisor import RestartPolicy, Supervisor
from ext_tests import ROOT
from ext_tests.test_extension_protocols import FakeHost, endpoint
from tests.support.waiting import wait_until
from tests.test_extensions_endpoints import FakePort

pytestmark = pytest.mark.skipif(
    os.environ.get("SYNTH_TEST_NETWORK") != "1",
    reason="downloads real libraries and models: set SYNTH_TEST_NETWORK=1",
)


def build_lib(tmp_path_factory: pytest.TempPathFactory, manifest_path: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build lib/")
    manifest = load_manifest(manifest_path)
    target = tmp_path_factory.mktemp("lib") / "lib"
    done = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            "3.12",
            "--target",
            str(target),
            *manifest.python_requirements,
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        pytest.skip(f"cannot build lib/: {done.stderr.strip()[-200:]}")
    return target


@pytest.mark.asyncio
async def test_kitten_speaks_and_vosk_hears_it(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ports, grants = memory_ports(), MemoryGrantStore()
    gateway = Gateway(grants=grants)
    register_core_api(gateway, ports)
    register_endpoint_api(gateway, FakePort())
    layout = RuntimeLayout(tmp_path / "extensions")
    media = MediaBridge(
        data_dir=layout.data_dir,
        uid=lambda ext: None,
        inbound_root=tmp_path / "media",
        resolve_outbound=lambda raw: (None, "refused"),
    )
    register_interface_api(
        gateway, InterfacePorts(MemoryInterfaceHost(), MemoryMessaging(), media)
    )
    host = FakeHost()
    attach_protocols(gateway, host, media)
    errors: list[tuple[str, str]] = []
    launcher = Launcher(
        gateway,
        layout,
        SandboxPolicy(drop_privileges=False),
        on_stderr=lambda ext, line: errors.append((ext, line)),
    )
    supervisor = Supervisor(
        launcher,
        RestartPolicy(
            initial_delay=0.05,
            max_delay=0.1,
            max_failures=3,
            hello_timeout=60.0,
            stop_grace=1.0,
            term_wait=0.5,
        ),
    )
    try:
        for name, protocol in (("vox_kitten", "kitten"), ("auris_vosk", "vosk")):
            source = ROOT / "extensions" / name
            manifest = load_manifest(source / "synth-extension.toml")
            directory = layout.install_dir(manifest.id)
            directory.mkdir(parents=True)
            for item in source.iterdir():
                if item.is_dir():
                    shutil.copytree(item, directory / item.name)
                elif item.suffix == ".py":
                    shutil.copy(item, directory / item.name)
            (directory / "lib").symlink_to(
                build_lib(tmp_path_factory, source / "synth-extension.toml")
            )
            grants.grant(manifest.id, *manifest.permissions.capabilities)
            supervisor.register(manifest)
            await supervisor.start(manifest.id)
            await wait_until(lambda p=protocol: p in host.factories, 60)

        await asyncio.sleep(
            2
        )  # the extension registers its handler after it said hello
        speech = await host.factories["kitten"](
            endpoint("kitten", base_url=""), ""
        ).generate_tts(
            "Hello, I am a synth, and this is synthetic heart.",
            "Bella",
            model="kitten-tts-nano-0.8",
        )
        assert speech is not None and speech[:4] == b"RIFF"

        heard: Any = await host.factories["vosk"](
            endpoint("vosk", base_url=""), ""
        ).transcribe_audio(speech, "audio/wav")
        assert heard is not None and "synthetic heart" in heard.lower()
        assert not [line for _, line in errors if "Traceback" in line]
    finally:
        await supervisor.stop_all()
        await gateway.close_all()
