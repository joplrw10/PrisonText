from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from pumpkinsync.app import create_app


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "src" / "pumpkinsync" / "private_template" / "template.json"


def wav_bytes(seconds: float = 0.8, sample_rate: int = 16_000) -> bytes:
    t = np.arange(int(seconds * sample_rate), dtype=np.float32) / sample_rate
    samples = np.zeros_like(t)
    active = (t > 0.15) & (t < seconds - 0.15)
    samples[active] = 0.5 * np.sin(2 * np.pi * 180 * t[active])
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(data_root=tmp_path / "data", template_path=TEMPLATE_PATH)
    return TestClient(app)


def create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        data={"mode": "skit", "analyzer": "builtin", "intensity": "1.0"},
        files={"audio": ("test voice.wav", wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_reports_local_capabilities(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ffmpeg"] is True
    assert payload["template"]["id"] == "trick-user-private"
    assert set(payload["supported_extensions"]) >= {".mp3", ".wav", ".m4a"}


def test_create_project_analyzes_audio_and_exposes_preview_data(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        payload = create_project(client)
        audio_response = client.get(payload["audio_url"])

    assert payload["status"] == "ready"
    assert 0.75 < payload["duration"] < 0.85
    assert payload["analyzer"] == "builtin"
    assert payload["cues"][0]["shape"] == "X"
    assert payload["cues"][-1]["shape"] == "X"
    assert len(payload["waveform"]) == 1200
    assert payload["template"]["frames"]["X"]["normal"]["center"].startswith("/template/")
    assert audio_response.status_code == 200
    assert audio_response.content.startswith(b"RIFF")


def test_create_project_rejects_unsupported_input(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/projects",
            data={"mode": "skit"},
            files={"audio": ("malware.exe", b"not audio", "application/octet-stream")},
        )

    assert response.status_code == 415
    assert "supported audio" in response.json()["detail"]


def test_cue_update_rejects_timeline_gap(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
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


def test_preview_export_job_completes_and_downloads_mp4(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        project = create_project(client)
        started = client.post(f"/api/projects/{project['id']}/export", json={"quality": "preview"})
        assert started.status_code == 202, started.text
        job_url = started.json()["job_url"]

        payload: dict = {}
        for _ in range(100):
            payload = client.get(job_url).json()
            if payload["status"] in {"complete", "error"}:
                break
            time.sleep(0.05)

        assert payload["status"] == "complete", payload
        download = client.get(payload["download_url"])

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("video/mp4")
    assert len(download.content) > 5_000


def test_recent_projects_endpoint_can_reopen_analyzed_project(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
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


def test_health_finds_rhubarb_in_launch_folder(tmp_path: Path, monkeypatch) -> None:
    binary = tmp_path / "tools" / "rhubarb" / "rhubarb"
    binary.parent.mkdir(parents=True)
    binary.write_text("placeholder", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with make_client(tmp_path) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["rhubarb"] is True
