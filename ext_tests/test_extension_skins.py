"""The skin extensions are files only, and ship a complete VRM skin."""

from __future__ import annotations

import json

import pytest

from core.extensions.manifest import load_manifest
from ext_tests import ROOT

SKINS = {"skin_miku": "Miku", "skin_riko": "Riko"}


@pytest.mark.parametrize("ext,skin", sorted(SKINS.items()))
def test_a_skin_extension_has_no_process_and_one_vrm_skin(ext: str, skin: str) -> None:
    base = ROOT / "extensions" / ext
    manifest = load_manifest(base / "synth-extension.toml")
    assert manifest.kind == "skins" and manifest.entry is None
    assert manifest.permissions.capabilities == ()
    (provided,) = manifest.provides.skins
    assert (provided.name, provided.type) == (skin, "vrm")
    folder = base / provided.path
    assert any(folder.glob("*.vrm")), "the skin needs its model"
    assert json.loads((folder / "persona.json").read_text(encoding="utf-8"))["name"]
    assert (folder / "preview.png").is_file()


def test_the_skins_are_published_but_not_installed_by_default() -> None:
    script = (ROOT / "scripts" / "build_release.sh").read_text(encoding="utf-8")
    extensions = next(l for l in script.splitlines() if l.startswith("EXTENSIONS="))
    defaults = next(l for l in script.splitlines() if l.startswith("DEFAULTS="))
    for ext in SKINS:
        assert ext in extensions
        assert f"official/{ext}" not in defaults
