from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "pumpkinsync" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def test_face_preview_updates_only_when_frame_url_changes() -> None:
    assert "currentFrameUrl: null" in APP_JS
    assert "function setFaceFrame(cue, index)" in APP_JS
    assert "if (url !== state.currentFrameUrl)" in APP_JS


def test_export_does_not_start_when_cue_save_fails() -> None:
    assert "const saved = await saveCues();" in APP_JS
    assert "if (!saved) throw new Error" in APP_JS


def test_recent_projects_are_loaded_and_can_be_reopened() -> None:
    assert 'id="recentProjects"' in INDEX
    assert "async function loadRecentProjects()" in APP_JS
    assert "openProject(project.id)" in APP_JS
    assert "loadRecentProjects();" in APP_JS


def test_every_element_id_used_by_the_script_exists() -> None:
    used = set(re.findall(r"\$\('#([A-Za-z][\w-]*)'\)", APP_JS))
    declared = set(re.findall(r'id="([^"]+)"', INDEX))

    assert used, "expected the script to look up elements by id"
    assert used <= declared, sorted(used - declared)


def test_user_supplied_text_is_never_written_with_inner_html() -> None:
    # Only the cue table uses innerHTML, and it contains numbers and validated shape letters.
    assert APP_JS.count("innerHTML") == 1
    assert "cueTable.innerHTML = rows.join('')" in APP_JS


def test_editor_keyboard_shortcuts_are_documented_in_the_page() -> None:
    for shortcut in ("Space", "Split", "Merge", "Undo", "Redo"):
        assert shortcut in INDEX


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_app_js_is_valid_javascript() -> None:
    subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True, capture_output=True, text=True)
