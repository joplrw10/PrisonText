from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "src" / "pumpkinsync" / "static" / "app.js").read_text(encoding="utf-8")


def test_face_preview_updates_only_when_frame_url_changes() -> None:
    assert "currentFrameUrl: null" in APP_JS
    assert "function setFaceFrame(cue, index)" in APP_JS
    assert "if (url !== state.currentFrameUrl)" in APP_JS
    assert "else face.src = frameFor" not in APP_JS


def test_export_does_not_start_when_cue_save_fails() -> None:
    assert "return false;" in APP_JS
    assert "const saved = await saveCues();" in APP_JS
    assert "if (!saved) throw new Error" in APP_JS


def test_recent_projects_are_loaded_and_can_be_reopened() -> None:
    html = (Path(__file__).resolve().parents[1] / "src" / "pumpkinsync" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="recentProjects"' in html
    assert "async function loadRecentProjects()" in APP_JS
    assert "project.project_url" in APP_JS
    assert "loadRecentProjects();" in APP_JS
