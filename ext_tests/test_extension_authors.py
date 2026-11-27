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


@pytest.mark.parametrize("name", ["synth_stage", "synth_dashboard"])
def test_the_stage_and_the_dashboard_are_frames_not_windows(name: str) -> None:
    """They fill the main area like the avatar's 3D page: they are not resizable windows."""
    manifest = load_manifest(ROOT / "extensions" / name / "synth-extension.toml")
    (entry,) = manifest.provides.ui
    assert (entry.slot, entry.presentation) == ("more", "frame")
