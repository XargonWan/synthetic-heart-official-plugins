"""The Dashboard as an extension: its manifest, the page's contract and its theme hand-off."""

from __future__ import annotations

from pathlib import Path

from core.extensions.manifest import load_manifest

SRC = Path(__file__).resolve().parent.parent


def test_manifest_puts_the_dashboard_in_the_more_menu() -> None:
    manifest = load_manifest(SRC / "synth-extension.toml")
    assert manifest.id == "official/synth_dashboard"
    assert [(u.slot, u.id) for u in manifest.provides.ui] == [("more", "dashboard")]
    web = manifest.provides.web
    assert web is not None and web.static == "web" and web.same_origin
    # It edits settings through /api as the person, so it needs the Core's origin.
    assert set(manifest.permissions.capabilities) == {"log", "web.static", "web.origin"}
    assert (SRC / "guide.md").is_file() and (SRC / "icon.svg").is_file()


def test_the_page_keeps_the_ids_the_script_drives() -> None:
    page = (SRC / "web" / "index.html").read_text(encoding="utf-8")
    for needle in (
        'id="tab-dashboard"',
        'id="dash-readouts"',
        'id="dash-search"',
        'id="dash-advanced"',
        'id="dash-refresh"',
        'id="dash-sidebar-count"',
        'id="dash-group-index"',
        'id="dash-rack"',
        'id="dash-toasts"',
        'src="theme.js"',
        'src="dashboard.js"',
    ):
        assert needle in page, f"missing {needle!r}"
    # Relative references only: the page is served under /x/<id>/ and /<alias>/.
    assert 'src="/' not in page and 'href="/' not in page
    # The script starts itself when its section is marked active.
    assert 'class="tab-panel active" id="tab-dashboard"' in page


def test_the_script_is_a_classic_script_that_talks_to_the_core_api() -> None:
    script = (SRC / "web" / "dashboard.js").read_text(encoding="utf-8")
    assert "export {" not in script and "\nimport " not in script
    assert "'/api/config'" in script
    # Not tied to the WebUI shell: no body dataset / active-tab lookups.
    assert "activeTab" not in script


def test_the_theme_hand_off_reads_the_parent_and_tolerates_having_none() -> None:
    theme = (SRC / "web" / "theme.js").read_text(encoding="utf-8")
    assert "window.parent" in theme and "try {" in theme
    for name in ("--bg", "--surface", "--accent", "--text", "--error"):
        assert f"'{name}'" in theme
