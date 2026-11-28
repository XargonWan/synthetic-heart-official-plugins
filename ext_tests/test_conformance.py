"""Rules every extension in this repository must keep (see AGENTS.md).

These are a lint, not the security boundary: the real boundary is the process and the
gateway. They catch the mistakes an agent (or a tired human) makes before they ship.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.extensions.manifest import load_manifest

EXTENSIONS = Path(__file__).resolve().parent.parent / "extensions"
FORBIDDEN = {"core", "plugins", "interface", "engines"}
SOURCES = sorted(EXTENSIONS.glob("*/synth-extension.toml"))


def python_files(extension: Path) -> list[Path]:
    return [p for p in extension.rglob("*.py") if "lib" not in p.relative_to(extension).parts]


@pytest.mark.parametrize("manifest", SOURCES, ids=lambda p: p.parent.name)
def test_the_manifest_is_valid(manifest: Path) -> None:
    parsed = load_manifest(manifest)
    assert parsed.entry is not None or parsed.kind == "skins"


@pytest.mark.parametrize("manifest", SOURCES, ids=lambda p: p.parent.name)
def test_nothing_imports_the_core(manifest: Path) -> None:
    for path in python_files(manifest.parent):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, (
                    f"{path.name} imports {name}: an extension talks to the Core only through synth_sdk"
                )
