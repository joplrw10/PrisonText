from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from pumpkinsync.app import create_app
from pumpkinsync.projects import ProjectStore


def wav_bytes(seconds: float = 0.8, sample_rate: int = 16_000, fmt: str = "WAV") -> bytes:
    t = np.arange(int(seconds * sample_rate), dtype=np.float32) / sample_rate
    samples = np.zeros_like(t)
    active = (t > 0.15) & (t < seconds - 0.15)
    samples[active] = 0.5 * np.sin(2 * np.pi * 180 * t[active])
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format=fmt, subtype="PCM_16")
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path: Path, builtin_manifest: Path):
    app = create_app(data_root=tmp_path / "data", template_path=builtin_manifest, application_root=tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def wait_for_job(client: TestClient, job_url: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        payload = client.get(job_url).json()
        if payload["status"] in {"complete", "error"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {payload}")


def create_project(client: TestClient, name: str = "test voice.wav", data: bytes | None = None, **form) -> dict:
    response = client.post(
        "/api/projects",
        data={"mode": "skit", "analyzer": "builtin", "intensity": "1.0", **form},
        files={"audio": (name, data if data is not None else wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 202, response.text
    job = wait_for_job(client, response.json()["job"]["job_url"])
    assert job["status"] == "complete", job
    project = client.get(f"/api/projects/{response.json()['project']['id']}")
    assert project.status_code == 200
    return project.json()


def test_health_reports_local_capabilities(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert payload["status"] == "ok"
    assert payload["ffmpeg"] is True
    assert payload["template"]["id"] == "builtin-pumpkin"
    assert any(item["id"] == "builtin-pumpkin" and item["default"] for item in payload["templates"])
    assert set(payload["supported_extensions"]) >= {".mp3", ".wav", ".m4a", ".mp4"}


def test_create_project_analyzes_in_background_and_exposes_preview_data(client: TestClient) -> None:
    payload = create_project(client)
    audio_response = client.get(payload["audio_url"])
    frame_url = payload["template"]["frames"]["X"]["normal"]["center"]
    frame = client.get(frame_url)

    assert payload["status"] == "ready"
    assert 0.75 < payload["duration"] < 0.85
    assert payload["analyzer"] == "builtin"
    assert payload["cues"][0]["shape"] == "X"
    assert payload["cues"][-1]["shape"] == "X"
    assert len(payload["waveform"]) == 1200
    assert payload["settings"]["template_id"] == "builtin-pumpkin"
    assert frame_url.startswith("/templates/builtin-pumpkin/")
    assert frame.status_code == 200 and frame.content.startswith(b"\x89PNG")
    assert audio_response.status_code == 200
    assert audio_response.content.startswith(b"RIFF")


def test_analysis_job_reports_progress_messages(client: TestClient) -> None:
    started = client.post(
        "/api/projects",
        data={"mode": "skit", "analyzer": "builtin"},
        files={"audio": ("voice.wav", wav_bytes(), "audio/wav")},
    ).json()

    job = wait_for_job(client, started["job"]["job_url"])

    assert started["project"]["status"] == "analyzing"
    assert job["kind"] == "analyze"
    assert job["progress"] == 1.0
    assert "cues" in job["message"]


def test_create_project_rejects_unsupported_input_without_leaving_files(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        data={"mode": "skit"},
        files={"audio": ("malware.exe", b"not audio", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert "supported audio" in response.json()["detail"]
    assert not list((tmp_path / "data" / "projects").iterdir())


def test_create_project_rejects_bad_settings_and_empty_upload(client: TestClient, tmp_path: Path) -> None:
    bad_mode = client.post("/api/projects", data={"mode": "opera"}, files={"audio": ("a.wav", wav_bytes(), "audio/wav")})
    bad_template = client.post(
        "/api/projects", data={"template_id": "nope"}, files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
    )
    empty = client.post("/api/projects", files={"audio": ("a.wav", b"", "audio/wav")})

    assert bad_mode.status_code == 422
    assert bad_template.status_code == 422
    assert empty.status_code == 422
    assert not list((tmp_path / "data" / "projects").iterdir())


def test_undecodable_audio_fails_the_job_and_marks_project_error(client: TestClient) -> None:
    response = client.post("/api/projects", files={"audio": ("broken.mp3", b"definitely not mp3" * 50, "audio/mpeg")})
    job = wait_for_job(client, response.json()["job"]["job_url"])
    project = client.get(f"/api/projects/{response.json()['project']['id']}").json()

    assert job["status"] == "error"
    assert job["error"]
    assert project["status"] == "error"
    assert project["error"]


def test_aiff_upload_gets_browser_playable_audio(client: TestClient) -> None:
    project = create_project(client, name="voice.aiff", data=wav_bytes(fmt="AIFF"))

    audio = client.get(project["audio_url"])

    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content.startswith(b"RIFF")


def test_cue_update_rejects_timeline_gap(client: TestClient) -> None:
    project = create_project(client)
    response = client.put(
        f"/api/projects/{project['id']}/cues",
        json={
            "cues": [
                {"start": 0.0, "end": 0.2, "shape": "X"},
                {"start": 0.3, "end": project["duration"], "shape": "C", "energy": 0.5},
            ]
        },
    )

    assert response.status_code == 422
    assert "gap" in response.json()["detail"]


def test_cue_update_saves_edits(client: TestClient) -> None:
    project = create_project(client)
    duration = project["duration"]
    cues = [
        {"start": 0.0, "end": 0.3, "shape": "X"},
        {"start": 0.3, "end": 0.5, "shape": "D", "energy": 0.9},
        {"start": 0.5, "end": duration, "shape": "A", "energy": 0.1},
    ]

    saved = client.put(f"/api/projects/{project['id']}/cues", json={"cues": cues})
    reopened = client.get(f"/api/projects/{project['id']}").json()

    assert saved.status_code == 200
    assert [cue["shape"] for cue in reopened["cues"]] == ["X", "D", "A"]


def test_preview_export_job_completes_lists_and_deletes_mp4(client: TestClient) -> None:
    project = create_project(client)
    client.patch(f"/api/projects/{project['id']}", json={"lead_ms": 40})
    started = client.post(f"/api/projects/{project['id']}/export", json={"quality": "preview"})
    assert started.status_code == 202, started.text

    payload = wait_for_job(client, started.json()["job_url"])
    assert payload["status"] == "complete", payload
    assert payload["progress"] == 1.0
    download = client.get(payload["download_url"])
    listed = client.get(f"/api/projects/{project['id']}/exports").json()["exports"]
    removed = client.delete(payload["download_url"])
    after = client.get(f"/api/projects/{project['id']}/exports").json()["exports"]

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("video/mp4")
    assert len(download.content) > 5_000
    assert payload["media"]["width"] == 960
    assert [item["name"] for item in listed] == [payload["output_name"]]
    assert "builtin-pumpkin" in payload["output_name"]
    assert removed.status_code == 200
    assert after == []


def test_export_rejects_unknown_quality(client: TestClient) -> None:
    project = create_project(client)

    response = client.post(f"/api/projects/{project['id']}/export", json={"quality": "8k"})

    assert response.status_code == 422


def test_recent_projects_endpoint_can_reopen_analyzed_project(client: TestClient) -> None:
    assert client.get("/api/projects").json() == {"projects": []}
    created = create_project(client)

    response = client.get("/api/projects")
    recent = response.json()["projects"]
    reopened = client.get(recent[0]["project_url"])

    assert response.status_code == 200
    assert len(recent) == 1
    assert recent[0]["id"] == created["id"]
    assert recent[0]["original_name"] == "test voice.wav"
    assert recent[0]["status"] == "ready"
    assert reopened.status_code == 200
    assert reopened.json()["id"] == created["id"]


def test_reanalysis_updates_settings_and_transcript(client: TestClient) -> None:
    project = create_project(client, transcript="hello pumpkin")
    assert project["transcript"].strip() == "hello pumpkin"

    started = client.post(
        f"/api/projects/{project['id']}/analyze",
        json={"mode": "song", "analyzer": "builtin", "intensity": 1.4, "transcript": ""},
    )
    assert started.status_code == 202, started.text
    job = wait_for_job(client, started.json()["job"]["job_url"])
    reopened = client.get(f"/api/projects/{project['id']}").json()

    assert job["status"] == "complete"
    assert reopened["status"] == "ready"
    assert reopened["settings"]["mode"] == "song"
    assert reopened["settings"]["intensity"] == 1.4
    assert reopened["transcript"] == ""


def test_project_settings_validate_template_and_lead(client: TestClient) -> None:
    project = create_project(client)
    url = f"/api/projects/{project['id']}"

    assert client.patch(url, json={"template_id": "missing"}).status_code == 422
    assert client.patch(url, json={"lead_ms": 9000}).status_code == 422
    updated = client.patch(url, json={"lead_ms": 66, "template_id": "builtin-pumpkin"})

    assert updated.status_code == 200
    assert updated.json()["settings"]["lead_ms"] == 66


def test_delete_project_removes_everything(client: TestClient, tmp_path: Path) -> None:
    project = create_project(client)

    deleted = client.delete(f"/api/projects/{project['id']}")

    assert deleted.status_code == 200
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert not (tmp_path / "data" / "projects" / project["id"]).exists()


def test_cue_sheet_round_trips_through_tsv_and_rejects_garbage(client: TestClient) -> None:
    project = create_project(client)
    url = f"/api/projects/{project['id']}/cuesheet"

    tsv = client.get(url, params={"format": "tsv"})
    rhubarb_json = client.get(url, params={"format": "json"})
    lines = tsv.text.splitlines()
    lines[1] = lines[1].split("\t")[0] + "\tD"  # hand-edit the second cue
    imported = client.post(url, files={"file": ("cues.tsv", "\n".join(lines) + "\n", "text/plain")})
    garbage = client.post(url, files={"file": ("cues.tsv", "0.0\tQ\n", "text/plain")})

    assert tsv.status_code == 200
    assert "attachment" in tsv.headers["content-disposition"]
    assert rhubarb_json.json()["mouthCues"][0]["value"] == "X"
    assert imported.status_code == 200, imported.text
    assert imported.json()["cues"][1]["shape"] == "D"
    assert client.get(f"/api/projects/{project['id']}").json()["analyzer"] == "imported"
    assert garbage.status_code == 422


def test_template_assets_are_confined_to_template_images(client: TestClient) -> None:
    assert client.get("/templates/builtin-pumpkin/template.json").status_code == 404
    assert client.get("/templates/builtin-pumpkin/../../pyproject.toml").status_code == 404
    assert client.get("/templates/unknown/preview.jpg").status_code == 404
    assert client.get("/templates/builtin-pumpkin/preview.jpg").status_code == 200


def test_health_finds_rhubarb_in_launch_folder(tmp_path: Path, builtin_manifest: Path) -> None:
    binary = tmp_path / "tools" / "rhubarb" / "rhubarb"
    binary.parent.mkdir(parents=True)
    binary.write_text("placeholder", encoding="utf-8")

    app = create_app(data_root=tmp_path / "data", template_path=builtin_manifest, application_root=tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["rhubarb"] is True


def test_startup_marks_interrupted_analysis(tmp_path: Path, builtin_manifest: Path) -> None:
    store = ProjectStore(tmp_path / "data" / "projects")
    project = store.create_project("song.wav")
    project.source_file.write_bytes(wav_bytes())
    store.update_metadata(project, status="analyzing")

    app = create_app(data_root=tmp_path / "data", template_path=builtin_manifest, application_root=tmp_path)
    with TestClient(app) as client:
        payload = client.get(f"/api/projects/{project.id}").json()
        listed = client.get("/api/projects").json()["projects"]

    assert payload["status"] == "error"
    assert "interrupted" in payload["error"]
    assert listed[0]["status"] == "error"
