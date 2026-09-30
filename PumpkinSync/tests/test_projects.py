from __future__ import annotations

from pathlib import Path

import pytest

from pumpkinsync.projects import ProjectStore, sanitize_filename


def test_sanitize_filename_strips_directories_and_unsafe_characters() -> None:
    assert sanitize_filename("../../My spooky song (final)!!.mp3") == "My_spooky_song_final.mp3"
    assert sanitize_filename(".mp3") == "audio.mp3"


def test_project_store_keeps_all_paths_inside_root(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")

    project = store.create_project("../../escape.mp3", settings={"mode": "skit"})

    assert project.root.parent == store.root
    assert project.source_file.parent == project.root / "source"
    assert project.source_file.name == "escape.mp3"
    assert project.source_file.resolve().is_relative_to(store.root.resolve())
    assert store.load_project(project.id).id == project.id

    with pytest.raises(ValueError, match="project id"):
        store.load_project("../escape")


def test_project_store_round_trips_json_atomically(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project("sample.wav")

    store.write_json(project, "cues.json", {"cues": [{"shape": "X"}]})

    assert store.read_json(project, "cues.json") == {"cues": [{"shape": "X"}]}
    assert not list(project.root.glob("*.tmp"))


def test_resolve_relative_rejects_traversal(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project("sample.wav")

    with pytest.raises(ValueError, match="inside project"):
        store.resolve_relative(project, "../../outside.txt")


def test_list_projects_returns_newest_first(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    older = store.create_project("older.wav")
    newer = store.create_project("newer.wav")
    store.update_metadata(older, created_at="2026-01-01T00:00:00+00:00")
    store.update_metadata(newer, created_at="2026-01-02T00:00:00+00:00")

    projects = store.list_projects(limit=10)

    assert [project.id for project in projects] == [newer.id, older.id]


def test_list_projects_rejects_invalid_limit(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")

    with pytest.raises(ValueError, match="limit"):
        store.list_projects(limit=0)
