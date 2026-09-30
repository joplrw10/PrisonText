from __future__ import annotations

import importlib.util
import mimetypes
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import soundfile as sf
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import analyze_waveform, extract_waveform_peaks
from .cues import validate_cues
from .models import Cue
from .projects import ProjectRecord, ProjectStore, sanitize_filename
from .render import render_project
from .templates import TemplateManifest
from .tools import (
    ExternalToolError,
    find_rhubarb,
    prepare_analysis_audio,
    probe_media,
    run_rhubarb,
    try_demucs_vocals,
)

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MAX_UPLOAD_BYTES = 700 * 1024 * 1024


@dataclass(slots=True)
class ExportJob:
    id: str
    project_id: str
    status: str = "queued"
    quality: str = "full"
    message: str = "Waiting to render"
    download_url: str | None = None
    output_name: str | None = None
    error: str | None = None
    media: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ExportJob] = {}

    def add(self, job: ExportJob) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def update(self, job_id: str, **updates: Any) -> ExportJob:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in updates.items():
                setattr(job, key, value)
            return job

    def get(self, job_id: str) -> ExportJob | None:
        with self._lock:
            return self._jobs.get(job_id)


def _default_template_path() -> Path:
    override = __import__("os").environ.get("PUMPKINSYNC_TEMPLATE")
    if override:
        return Path(override).expanduser().resolve()
    package_candidate = Path(__file__).resolve().parent / "private_template" / "template.json"
    if package_candidate.is_file():
        return package_candidate
    cwd_candidate = Path.cwd() / "templates" / "trick-user" / "template.json"
    return cwd_candidate.resolve()


def _default_application_root() -> Path:
    override = __import__("os").environ.get("PUMPKINSYNC_HOME")
    if override:
        return Path(override).expanduser().resolve()
    cwd = Path.cwd().resolve()
    source_candidate = Path(__file__).resolve().parents[2]
    for candidate in (cwd, source_candidate):
        if (candidate / "tools").is_dir() or (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


def _default_data_root() -> Path:
    override = __import__("os").environ.get("PUMPKINSYNC_DATA")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / "data").resolve()


def _load_cues(store: ProjectStore, project: ProjectRecord) -> list[Cue]:
    payload = store.read_json(project, "cues.json")
    return [Cue.from_dict(value) for value in payload["cues"]]


def _project_payload(store: ProjectStore, project: ProjectRecord, template: TemplateManifest) -> dict[str, Any]:
    metadata = project.metadata
    cues_payload = store.read_json(project, "cues.json") if project.cues_file.is_file() else {"cues": []}
    waveform_payload = (
        store.read_json(project, "waveform.json") if project.waveform_file.is_file() else {"waveform": []}
    )
    return {
        "id": project.id,
        "status": metadata.get("status", "unknown"),
        "original_name": metadata.get("original_name"),
        "duration": float(metadata.get("duration", 0.0)),
        "mode": metadata.get("settings", {}).get("mode", "skit"),
        "analyzer": metadata.get("analyzer", "unknown"),
        "warnings": metadata.get("warnings", []),
        "cues": cues_payload.get("cues", []),
        "waveform": waveform_payload.get("waveform", []),
        "audio_url": f"/api/projects/{project.id}/audio",
        "template": template.public_dict("/template"),
    }


def create_app(
    *,
    data_root: Path | str | None = None,
    template_path: Path | str | None = None,
) -> FastAPI:
    resolved_data = Path(data_root).resolve() if data_root is not None else _default_data_root()
    resolved_template = (
        Path(template_path).resolve() if template_path is not None else _default_template_path()
    )
    template = TemplateManifest.load(resolved_template)
    store = ProjectStore(resolved_data / "projects")
    jobs = JobRegistry()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pumpkinsync-export")
    static_dir = Path(__file__).resolve().parent / "static"
    repository_root = _default_application_root()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        executor.shutdown(wait=True, cancel_futures=False)

    app = FastAPI(
        title="PumpkinSync",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.template = template
    app.state.jobs = jobs

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "rhubarb": find_rhubarb(project_root=repository_root) is not None,
            "demucs": importlib.util.find_spec("demucs") is not None,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "template": {
                "id": template.id,
                "name": template.name,
                "width": template.width,
                "height": template.height,
            },
        }

    @app.get("/api/template")
    def get_template() -> dict[str, Any]:
        return template.public_dict("/template")

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        projects = []
        for project in store.list_projects(limit=12):
            metadata = project.metadata
            if metadata.get("status") != "ready" or not project.cues_file.is_file():
                continue
            projects.append(
                {
                    "id": project.id,
                    "original_name": metadata.get("original_name", project.source_file.name),
                    "created_at": metadata.get("created_at"),
                    "duration": float(metadata.get("duration", 0.0)),
                    "mode": metadata.get("settings", {}).get("mode", "skit"),
                    "analyzer": metadata.get("analyzer", "unknown"),
                    "status": metadata.get("status", "unknown"),
                    "project_url": f"/api/projects/{project.id}",
                }
            )
        return {"projects": projects}

    @app.post("/api/projects")
    async def create_project_endpoint(
        audio: UploadFile = File(...),
        mode: str = Form("skit"),
        analyzer: str = Form("auto"),
        intensity: float = Form(1.0),
        separate_vocals: bool = Form(False),
        transcript: str = Form(""),
    ) -> dict[str, Any]:
        mode = mode.lower().strip()
        analyzer = analyzer.lower().strip()
        if mode not in {"skit", "song"}:
            raise HTTPException(status_code=422, detail="mode must be 'skit' or 'song'")
        if analyzer not in {"auto", "builtin", "rhubarb"}:
            raise HTTPException(status_code=422, detail="analyzer must be auto, builtin, or rhubarb")
        if not 0.4 <= intensity <= 1.8:
            raise HTTPException(status_code=422, detail="intensity must be between 0.4 and 1.8")
        filename = audio.filename or "audio"
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail="Upload a supported audio file: MP3, WAV, M4A, AAC, FLAC, OGG, or OPUS.",
            )

        project = store.create_project(
            filename,
            settings={
                "mode": mode,
                "analyzer": analyzer,
                "intensity": intensity,
                "separate_vocals": separate_vocals,
            },
        )
        total = 0
        try:
            with project.source_file.open("wb") as destination:
                while chunk := await audio.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="audio file is larger than 700 MB")
                    destination.write(chunk)
            if total == 0:
                raise HTTPException(status_code=422, detail="the uploaded audio file is empty")

            warnings: list[str] = []
            analysis_source = project.source_file
            if mode == "song" and separate_vocals:
                vocal_source, warning = try_demucs_vocals(project.source_file, project.root / "demucs")
                if warning:
                    warnings.append(warning)
                if vocal_source:
                    analysis_source = vocal_source

            transcript_path: Path | None = None
            if transcript.strip():
                transcript_path = project.root / "transcript.txt"
                transcript_path.write_text(transcript.strip() + "\n", encoding="utf-8")

            prepare_analysis_audio(analysis_source, project.analysis_file)
            samples, sample_rate = sf.read(project.analysis_file, dtype="float32", always_2d=False)
            duration = len(samples) / float(sample_rate)
            if duration <= 0:
                raise ValueError("the audio contains no playable samples")

            used_analyzer = "builtin"
            cues: list[Cue]
            if analyzer in {"auto", "rhubarb"}:
                rhubarb = find_rhubarb(project_root=repository_root)
                if rhubarb is None and analyzer == "rhubarb":
                    raise ValueError("Rhubarb was selected but its executable was not found")
                if rhubarb is not None:
                    try:
                        cues = run_rhubarb(
                            project.analysis_file,
                            duration=duration,
                            transcript_file=transcript_path,
                            executable=rhubarb,
                        )
                        used_analyzer = "rhubarb"
                    except (ExternalToolError, ValueError) as exc:
                        if analyzer == "rhubarb":
                            raise
                        warnings.append(f"Rhubarb failed; used the built-in analyzer instead. {exc}")
                        cues = analyze_waveform(samples, sample_rate, intensity=intensity)
                else:
                    cues = analyze_waveform(samples, sample_rate, intensity=intensity)
            else:
                cues = analyze_waveform(samples, sample_rate, intensity=intensity)

            validate_cues(cues, duration, tolerance=0.08)
            waveform = extract_waveform_peaks(samples, points=1200)
            store.write_json(
                project,
                "cues.json",
                {"duration": duration, "analyzer": used_analyzer, "cues": [cue.to_dict() for cue in cues]},
            )
            store.write_json(project, "waveform.json", {"waveform": waveform})
            project = store.update_metadata(
                project,
                status="ready",
                duration=duration,
                analyzer=used_analyzer,
                warnings=warnings,
                source_bytes=total,
            )
            return _project_payload(store, project, template)
        except HTTPException:
            raise
        except (ExternalToolError, ValueError, RuntimeError, sf.LibsndfileError) as exc:
            store.update_metadata(project, status="error", error=str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await audio.close()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        try:
            project = store.load_project(project_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="project was not found") from exc
        return _project_payload(store, project, template)

    @app.get("/api/projects/{project_id}/audio")
    def get_audio(project_id: str) -> FileResponse:
        try:
            project = store.load_project(project_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="project was not found") from exc
        if not project.source_file.is_file():
            raise HTTPException(status_code=404, detail="project audio was not found")
        media_type = mimetypes.guess_type(project.source_file.name)[0] or "application/octet-stream"
        return FileResponse(project.source_file, media_type=media_type, filename=project.source_file.name)

    @app.put("/api/projects/{project_id}/cues")
    def update_cues(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            project = store.load_project(project_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="project was not found") from exc
        duration = float(project.metadata.get("duration", 0.0))
        try:
            cues = [Cue.from_dict(value) for value in payload.get("cues", [])]
            validate_cues(cues, duration, tolerance=0.02)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.write_json(
            project,
            "cues.json",
            {
                "duration": duration,
                "analyzer": project.metadata.get("analyzer", "manual"),
                "cues": [cue.to_dict() for cue in cues],
            },
        )
        return {"status": "saved", "cues": [cue.to_dict() for cue in cues]}

    def export_worker(job_id: str, project_id: str, quality: str) -> None:
        try:
            jobs.update(job_id, status="running", message="Rendering face animation and audio")
            project = store.load_project(project_id)
            cues = _load_cues(store, project)
            stem = sanitize_filename(Path(project.source_file.name).stem).rsplit(".", 1)[0]
            if quality == "preview":
                output_name = f"PumpkinSync_{stem}_preview.mp4"
                width, height, preview = 960, 540, True
            else:
                output_name = f"PumpkinSync_{stem}_1080p.mp4"
                width, height, preview = 1920, 1080, False
            output = project.exports_dir / output_name
            render_project(
                audio_path=project.source_file,
                cues=cues,
                template=template,
                output_path=output,
                width=width,
                height=height,
                fps=30,
                preview=preview,
            )
            media = probe_media(output)
            jobs.update(
                job_id,
                status="complete",
                message="Export complete",
                output_name=output_name,
                download_url=f"/api/projects/{project_id}/exports/{output_name}",
                media=asdict(media),
            )
        except Exception as exc:  # surfaced verbatim in local-only job status
            jobs.update(job_id, status="error", message="Export failed", error=str(exc))

    @app.post("/api/projects/{project_id}/export", status_code=202)
    def start_export(project_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        try:
            project = store.load_project(project_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="project was not found") from exc
        if project.metadata.get("status") != "ready" or not project.cues_file.is_file():
            raise HTTPException(status_code=409, detail="project is not ready to export")
        quality = str(payload.get("quality", "full")).lower()
        if quality not in {"preview", "full"}:
            raise HTTPException(status_code=422, detail="quality must be preview or full")
        job = ExportJob(id=uuid.uuid4().hex, project_id=project_id, quality=quality)
        jobs.add(job)
        executor.submit(export_worker, job.id, project_id, quality)
        return {"job_id": job.id, "job_url": f"/api/jobs/{job.id}", "status": job.status}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job was not found")
        return job.public_dict()

    @app.get("/api/projects/{project_id}/exports/{filename}")
    def download_export(project_id: str, filename: str) -> FileResponse:
        try:
            project = store.load_project(project_id)
            safe_name = sanitize_filename(filename)
            output = store.resolve_relative(project, Path("exports") / safe_name)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="export was not found") from exc
        if not output.is_file() or output.suffix.lower() != ".mp4":
            raise HTTPException(status_code=404, detail="export was not found")
        return FileResponse(output, media_type="video/mp4", filename=output.name)

    app.mount("/template", StaticFiles(directory=template.root), name="template")
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", media_type="text/html")

    return app


app = create_app()
