"""Every extension names its author: XargonWan, except the Stage and the Dashboard."""

from __future__ import annotations

import pytest

from core.extensions.manifest import load_manifest
from ext_tests import ROOT

SCARLET = {"synth_stage", "synth_dashboard"}
NAMES = sorted(p.name for p in (ROOT / "extensions").iterdir() if p.is_dir())


@pytest.mark.parametrize("name", NAMES)
def test_the_author_is_named(name: str) -> None:
    manifest = load_manifest(ROOT / "extensions" / name / "synth-extension.toml")
    expected = ("Scarlet-Raine",) if name in SCARLET else ("XargonWan",)
    assert manifest.authors == expected
