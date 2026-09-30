from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .builtin_template import ensure_builtin_template
from .cues import validate_cues
from .jobs import Job, JobRegistry
from .models import Cue
from .pipeline import AnalysisSettings, run_analysis, write_transcript
from .projects import ProjectRecord, ProjectStore, sanitize_filename
from .render import QUALITY_PRESETS, render_project
from .templates import TemplateLibrary, TemplateManifest
from .tools import cues_to_tsv, find_rhubarb, parse_cue_sheet, prepare_playback_audio, probe_media

logger = logging.getLogger("pumpkinsync")

SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".aif", ".aiff", ".wma",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv",
}
# Browsers cannot reliably play these, so a WAV copy is made for the editor.
NEEDS_PLAYBACK_COPY = {".aif", ".aiff", ".wma", ".mkv", ".mov", ".m4v"}
AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".mp4": "video/mp4",
    ".webm": "video/webm",
}
MAX_UPLOAD_BYTES = 700 * 1024 * 1024
MAX_CUE_SHEET_BYTES = 5 * 1024 * 1024
LEAD_MS_RANGE = (-250.0, 500.0)


def _default_template_paths() -> list[Path]:
    override = os.environ.get("PUMPKINSYNC_TEMPLATE")
    return [Path(override).expanduser().resolve()] if override else []


def _default_application_root() -> Path:
    override = os.environ.get("PUMPKINSYNC_HOME")
    if override:
        return Path(override).expanduser().resolve()
    cwd = Path.cwd().resolve()
    source_candidate = Path(__file__).resolve().parents[2]
    for candidate in (cwd, source_candidate):
        if (candidate / "tools").is_dir() or (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


def _default_data_root() -> Path:
    override = os.environ.get("PUMPKINSYNC_DATA")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / "data").resolve()


def _default_builtin_dir(data_root: Path) -> Path:
    override = os.environ.get("PUMPKINSYNC_BUILTIN_TEMPLATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return data_root / ".cache" / "builtin-pumpkin"


def _load_cues(store: ProjectStore, project: ProjectRecord) -> list[Cue]:
    payload = store.read_json(project, "cues.json")
    return [Cue.from_dict(value) for value in payload["cues"]]


def _timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def create_app(
    *,
    data_root: Path | str | None = None,
    template_path: Path | str | None = None,
    builtin_template_dir: Path | str | None = None,
    application_root: Path | str | None = None,
) -> FastAPI:
    resolved_data = Path(data_root).resolve() if data_root is not None else _default_data_root()
    resolved_data.mkdir(parents=True, exist_ok=True)
    repository_root = Path(application_root).resolve() if application_root else _default_application_root()
    builtin_dir = Path(builtin_template_dir).resolve() if builtin_template_dir else _default_builtin_dir(resolved_data)
    explicit = [Path(template_path).resolve()] if template_path is not None else _default_template_paths()

    builtin_manifest = ensure_builtin_template(builtin_dir)
    library = TemplateLibrary.discover(
        explicit=explicit,
        package_private=Path(__file__).resolve().parent / "private_template" / "template.json",
        user_dirs=[resolved_data / "templates", repository_root / "templates"],
        builtin=builtin_manifest,
    )
    for message in library.errors:
        logger.warning(message)

    store = ProjectStore(resolved_data / "projects")
    jobs = JobRegistry()
    # One analysis at a time: Demucs alone can saturate a machine.
    analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pumpkinsync-analysis")
    export_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pumpkinsync-export")
    static_dir = Path(__file__).resolve().parent / "static"

    _recover_interrupted_projects(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        analysis_pool.shutdown(wait=True, cancel_futures=True)
        export_pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(
        title="PumpkinSync",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.templates = library
    app.state.template = library.default
    app.state.jobs = jobs

    # ------------------------------------------------------------------ helpers

    def load_or_404(project_id: str) -> ProjectRecord:
        try:
            return store.load_project(project_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="project was not found") from exc

    def template_for(project: ProjectRecord) -> TemplateManifest:
        return library.get(project.metadata.get("settings", {}).get("template_id"))

    def exports_payload(project: ProjectRecord) -> list[dict[str, Any]]:
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": _timestamp(path),
                "download_url": f"/api/projects/{project.id}/exports/{path.name}",
            }
            for path in store.list_exports(project)
        ]

    def project_payload(project: ProjectRecord) -> dict[str, Any]:
        metadata = project.metadata
        settings = metadata.get("settings", {})
        cues_payload = store.read_json(project, "cues.json") if project.cues_file.is_file() else {"cues": []}
        waveform_payload = (
            store.read_json(project, "waveform.json") if project.waveform_file.is_file() else {"waveform": []}
        )
        template = template_for(project)
        warnings = list(metadata.get("warnings", []))
        requested_template = settings.get("template_id")
        if requested_template and requested_template not in library:
            warnings.append(f"Template {requested_template!r} is not installed; showing {template.name}.")
        transcript = (
            project.transcript_file.read_text(encoding="utf-8") if project.transcript_file.is_file() else ""
        )
        return {
            "id": project.id,
            "status": metadata.get("status", "unknown"),
            "error": metadata.get("error"),
            "original_name": project.display_name,
            "created_at": metadata.get("created_at"),
            "duration": float(metadata.get("duration", 0.0)),
            "mode": settings.get("mode", "skit"),
            "analyzer": cues_payload.get("analyzer", metadata.get("analyzer", "unknown")),
            "settings": {
                "mode": settings.get("mode", "skit"),
                "analyzer": settings.get("analyzer", "auto"),
                "intensity": float(settings.get("intensity", 1.0)),
                "separate_vocals": bool(settings.get("separate_vocals", False)),
                "template_id": template.id,
                "lead_ms": float(settings.get("lead_ms", 0.0)),
            },
            "warnings": warnings,
            "transcript": transcript,
            "cues": cues_payload.get("cues", []),
            "waveform": waveform_payload.get("waveform", []),
            "audio_url": f"/api/projects/{project.id}/audio",
            "template": template.public_dict(),
            "exports": exports_payload(project),
            "active_jobs": [job.public_dict() for job in jobs.for_project(project.id) if job.active],
        }

    def project_summary(project: ProjectRecord) -> dict[str, Any]:
        metadata = project.metadata
        active = jobs.find_active(project.id, "analyze")
        return {
            "id": project.id,
            "original_name": project.display_name,
            "created_at": metadata.get("created_at"),
            "duration": float(metadata.get("duration", 0.0)),
            "mode": metadata.get("settings", {}).get("mode", "skit"),
            "analyzer": metadata.get("analyzer", "unknown"),
            "status": metadata.get("status", "unknown"),
            "error": metadata.get("error"),
            "project_url": f"/api/projects/{project.id}",
            "active_job": active.public_dict() if active else None,
        }

    def submit_analysis(project: ProjectRecord, settings: AnalysisSettings) -> Job:
        job = jobs.add(Job(kind="analyze", project_id=project.id, message="Queued for analysis"))
        analysis_pool.submit(analysis_worker, job.id, project.id, settings)
        return job

    def analysis_worker(job_id: str, project_id: str, settings: AnalysisSettings) -> None:
        jobs.update(job_id, status="running", progress=0.02, message="Starting analysis")
        project: ProjectRecord | None = None
        try:
            project = store.load_project(project_id)
            if project.source_file.suffix.lower() in NEEDS_PLAYBACK_COPY:
                jobs.update(job_id, progress=0.03, message="Preparing browser playback audio")
                prepare_playback_audio(project.source_file, project.root / "playback.wav")
            project = run_analysis(
                store,
                project,
                settings,
                application_root=repository_root,
                progress=lambda fraction, message: jobs.update(job_id, progress=fraction, message=message),
            )
            cue_count = len(store.read_json(project, "cues.json")["cues"])
            jobs.update(
                job_id,
                status="complete",
                progress=1.0,
                message=f"Ready: {cue_count} cues from the {project.metadata.get('analyzer')} analyzer",
                result={
                    "project_url": f"/api/projects/{project_id}",
                    "warnings": project.metadata.get("warnings", []),
                },
            )
        except Exception as exc:  # reported to the local user through the job
            logger.exception("analysis failed for project %s", project_id)
            if project is not None:
                # A failed re-analysis keeps the previous, still-valid timeline.
                has_cues = project.cues_file.is_file()
                store.update_metadata(project, status="ready" if has_cues else "error", error=str(exc))
            jobs.update(job_id, status="error", message="Analysis failed", error=str(exc))

    def export_worker(job_id: str, project_id: str, quality: str) -> None:
        try:
            jobs.update(job_id, status="running", progress=0.01, message="Preparing render")
            project = store.load_project(project_id)
            cues = _load_cues(store, project)
            template = template_for(project)
            preset = QUALITY_PRESETS[quality]
            lead_seconds = float(project.metadata.get("settings", {}).get("lead_ms", 0.0)) / 1000.0
            stem = sanitize_filename(Path(project.source_file.name).stem).rsplit(".", 1)[0]
            output_name = sanitize_filename(f"PumpkinSync_{stem}_{template.id}_{preset['suffix']}.mp4")
            output = project.exports_dir / output_name

            def on_progress(fraction: float) -> None:
                jobs.update(job_id, progress=0.02 + 0.96 * fraction, message=f"Rendering video… {round(fraction * 100)}%")

            render_project(
                audio_path=project.source_file,
                cues=cues,
                template=template,
                output_path=output,
                width=int(preset["width"]),
                height=int(preset["height"]),
                fps=30,
                preview=bool(preset["preview"]),
                lead_seconds=lead_seconds,
                on_progress=on_progress,
            )
            media = probe_media(output)
            jobs.update(
                job_id,
                status="complete",
                progress=1.0,
                message="Export complete",
                result={
                    "output_name": output_name,
                    "download_url": f"/api/projects/{project_id}/exports/{output_name}",
                    "media": asdict(media),
                },
            )
        except Exception as exc:  # reported to the local user through the job
            logger.exception("export failed for project %s", project_id)
            jobs.update(job_id, status="error", message="Export failed", error=str(exc))

    # --------------------------------------------------------------- endpoints

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        rhubarb = find_rhubarb(project_root=repository_root)
        default = library.default
        return {
            "status": "ok",
            "version": __version__,
            "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "rhubarb": rhubarb is not None,
            "demucs": importlib.util.find_spec("demucs") is not None,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "template": {"id": default.id, "name": default.name, "width": default.width, "height": default.height},
            "templates": library.summaries(),
            "template_errors": list(library.errors),
        }

    @app.get("/api/template")
    def get_template() -> dict[str, Any]:
        return library.default.public_dict()

    @app.get("/api/templates")
    def list_templates() -> dict[str, Any]:
        return {"templates": library.summaries(), "default_id": library.default_id}

    @app.get("/api/templates/{template_id}")
    def get_template_by_id(template_id: str) -> dict[str, Any]:
        if template_id not in library:
            raise HTTPException(status_code=404, detail="template was not found")
        return library.get(template_id).public_dict()

    @app.get("/templates/{template_id}/{asset_path:path}", include_in_schema=False)
    def template_asset(template_id: str, asset_path: str) -> FileResponse:
        if template_id not in library:
            raise HTTPException(status_code=404, detail="template was not found")
        try:
            path = library.get(template_id).resolve_asset(asset_path)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="template asset was not found") from exc
        return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/projects")
    def list_projects(limit: int = Query(24, ge=1, le=100)) -> dict[str, Any]:
        projects = [
            project_summary(project)
            for project in store.list_projects(limit=100)
            if project.metadata.get("status") in {"ready", "analyzing", "error"}
        ]
        return {"projects": projects[:limit]}

    @app.post("/api/projects", status_code=202)
    async def create_project_endpoint(
        audio: UploadFile = File(...),
        mode: str = Form("skit"),
        analyzer: str = Form("auto"),
        intensity: float = Form(1.0),
        separate_vocals: bool = Form(False),
        transcript: str = Form(""),
        template_id: str = Form(""),
        lead_ms: float = Form(0.0),
    ) -> dict[str, Any]:
        try:
            settings = AnalysisSettings.build(
                mode=mode, analyzer=analyzer, intensity=intensity, separate_vocals=separate_vocals
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if template_id and template_id not in library:
            raise HTTPException(status_code=422, detail="the selected face template is not installed")
        if not LEAD_MS_RANGE[0] <= lead_ms <= LEAD_MS_RANGE[1]:
            raise HTTPException(status_code=422, detail="mouth lead must be between -250 and 500 ms")
        filename = audio.filename or "audio"
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail="Upload a supported audio file: MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, AIFF, WMA, "
                "or a video (MP4, MOV, WEBM, MKV) whose soundtrack should be used.",
            )
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            raise HTTPException(
                status_code=503,
                detail="FFmpeg and FFprobe are required. Run the setup script or install FFmpeg, then restart PumpkinSync.",
            )

        project = store.create_project(
            filename,
            settings={
                **settings.to_dict(),
                "template_id": template_id or library.default_id,
                "lead_ms": float(lead_ms),
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
            write_transcript(project, transcript)
        except HTTPException:
            store.delete_project(project)
            raise
        except ValueError as exc:
            store.delete_project(project)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await audio.close()

        project = store.update_metadata(project, status="analyzing", source_bytes=total)
        job = submit_analysis(project, settings)
        return {"project": project_payload(project), "job": job.public_dict()}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return project_payload(load_or_404(project_id))

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project = load_or_404(project_id)
        settings = dict(project.metadata.get("settings", {}))
        if "template_id" in payload:
            template_id = str(payload["template_id"])
            if template_id not in library:
                raise HTTPException(status_code=422, detail="the selected face template is not installed")
            settings["template_id"] = template_id
        if "lead_ms" in payload:
            try:
                lead_ms = float(payload["lead_ms"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="mouth lead must be a number") from exc
            if not LEAD_MS_RANGE[0] <= lead_ms <= LEAD_MS_RANGE[1]:
                raise HTTPException(status_code=422, detail="mouth lead must be between -250 and 500 ms")
            settings["lead_ms"] = lead_ms
        project = store.update_metadata(project, settings=settings)
        return project_payload(project)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, Any]:
        project = load_or_404(project_id)
        if jobs.find_active(project.id) is not None:
            raise HTTPException(status_code=409, detail="wait for the running analysis or export to finish first")
        store.delete_project(project)
        return {"status": "deleted", "id": project_id}

    @app.post("/api/projects/{project_id}/analyze", status_code=202)
    def reanalyze_project(project_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        project = load_or_404(project_id)
        if jobs.find_active(project.id, "analyze") is not None:
            raise HTTPException(status_code=409, detail="this project is already being analyzed")
        if not project.source_file.is_file():
            raise HTTPException(status_code=409, detail="the project's source audio is missing")
        current = project.metadata.get("settings", {})
        try:
            settings = AnalysisSettings.build(
                mode=payload.get("mode", current.get("mode", "skit")),
                analyzer=payload.get("analyzer", current.get("analyzer", "auto")),
                intensity=payload.get("intensity", current.get("intensity", 1.0)),
                separate_vocals=payload.get("separate_vocals", current.get("separate_vocals", False)),
            )
            if "transcript" in payload:
                write_transcript(project, str(payload["transcript"] or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project = store.update_metadata(project, status="analyzing", error=None)
        job = submit_analysis(project, settings)
        return {"project": project_payload(project), "job": job.public_dict()}

    @app.get("/api/projects/{project_id}/audio")
    def get_audio(project_id: str) -> FileResponse:
        project = load_or_404(project_id)
        playback = project.root / "playback.wav"
        if project.source_file.suffix.lower() in NEEDS_PLAYBACK_COPY and playback.is_file():
            return FileResponse(playback, media_type="audio/wav")
        if not project.source_file.is_file():
            raise HTTPException(status_code=404, detail="project audio was not found")
        suffix = project.source_file.suffix.lower()
        media_type = AUDIO_MEDIA_TYPES.get(suffix, "application/octet-stream")
        return FileResponse(project.source_file, media_type=media_type, filename=project.source_file.name)

    @app.put("/api/projects/{project_id}/cues")
    def update_cues(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project = load_or_404(project_id)
        if project.metadata.get("status") != "ready":
            raise HTTPException(status_code=409, detail="cues can be edited once analysis has finished")
        duration = float(project.metadata.get("duration", 0.0))
        try:
            cues = [Cue.from_dict(value) for value in payload.get("cues", [])]
            validate_cues(cues, duration, tolerance=0.02)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        previous = store.read_json(project, "cues.json") if project.cues_file.is_file() else {}
        store.write_json(
            project,
            "cues.json",
            {
                "duration": duration,
                "analyzer": previous.get("analyzer", project.metadata.get("analyzer", "manual")),
                "cues": [cue.to_dict() for cue in cues],
            },
        )
        return {"status": "saved", "cues": [cue.to_dict() for cue in cues]}

    @app.get("/api/projects/{project_id}/cuesheet")
    def download_cue_sheet(project_id: str, format: str = Query("tsv", pattern="^(tsv|json)$")) -> Response:
        project = load_or_404(project_id)
        if not project.cues_file.is_file():
            raise HTTPException(status_code=409, detail="this project has no cues yet")
        stem = sanitize_filename(Path(project.source_file.name).stem).rsplit(".", 1)[0]
        cues = _load_cues(store, project)
        if format == "json":
            body = json.dumps(
                {
                    "metadata": {"soundFile": project.display_name, "duration": float(project.metadata.get("duration", 0.0))},
                    "mouthCues": [
                        {"start": round(cue.start, 3), "end": round(cue.end, 3), "value": cue.shape} for cue in cues
                    ],
                },
                indent=2,
            )
            media_type, filename = "application/json", f"{stem}_cues.json"
        else:
            body, media_type, filename = cues_to_tsv(cues), "text/tab-separated-values", f"{stem}_cues.tsv"
        return Response(
            content=body,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/projects/{project_id}/cuesheet")
    async def import_cue_sheet(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        project = load_or_404(project_id)
        if project.metadata.get("status") != "ready":
            raise HTTPException(status_code=409, detail="cues can be imported once analysis has finished")
        raw = await file.read(MAX_CUE_SHEET_BYTES + 1)
        await file.close()
        if len(raw) > MAX_CUE_SHEET_BYTES:
            raise HTTPException(status_code=413, detail="cue sheet is larger than 5 MB")
        duration = float(project.metadata.get("duration", 0.0))
        try:
            cues = parse_cue_sheet(raw.decode("utf-8-sig", errors="strict"), duration)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="cue sheet must be UTF-8 text") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.write_json(
            project,
            "cues.json",
            {"duration": duration, "analyzer": "imported", "cues": [cue.to_dict() for cue in cues]},
        )
        return {"status": "imported", "cues": [cue.to_dict() for cue in cues]}

    @app.post("/api/projects/{project_id}/export", status_code=202)
    def start_export(project_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        project = load_or_404(project_id)
        if project.metadata.get("status") != "ready" or not project.cues_file.is_file():
            raise HTTPException(status_code=409, detail="project is not ready to export")
        quality = str(payload.get("quality", "full")).lower()
        if quality not in QUALITY_PRESETS:
            raise HTTPException(status_code=422, detail="quality must be preview or full")
        existing = jobs.find_active(project.id, "export", quality=quality)
        if existing is not None:
            job = existing
        else:
            job = jobs.add(Job(kind="export", project_id=project.id, options={"quality": quality}, message="Queued for rendering"))
            export_pool.submit(export_worker, job.id, project.id, quality)
        return {"job_id": job.id, "job_url": f"/api/jobs/{job.id}", "status": job.status, "job": job.public_dict()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job was not found")
        payload = job.public_dict()
        # Flatten the export result for older clients.
        if job.kind == "export" and job.result:
            payload.update(job.result)
        return payload

    @app.get("/api/projects/{project_id}/exports")
    def list_exports(project_id: str) -> dict[str, Any]:
        return {"exports": exports_payload(load_or_404(project_id))}

    def export_path_or_404(project_id: str, filename: str) -> Path:
        try:
            project = store.load_project(project_id)
            output = store.resolve_relative(project, Path("exports") / sanitize_filename(filename))
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="export was not found") from exc
        if not output.is_file() or output.suffix.lower() != ".mp4":
            raise HTTPException(status_code=404, detail="export was not found")
        return output

    @app.get("/api/projects/{project_id}/exports/{filename}")
    def download_export(project_id: str, filename: str) -> FileResponse:
        output = export_path_or_404(project_id, filename)
        return FileResponse(output, media_type="video/mp4", filename=output.name)

    @app.delete("/api/projects/{project_id}/exports/{filename}")
    def delete_export(project_id: str, filename: str) -> dict[str, Any]:
        output = export_path_or_404(project_id, filename)
        output.unlink()
        return {"status": "deleted", "name": output.name}

    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    return app


def _recover_interrupted_projects(store: ProjectStore) -> None:
    """Projects left mid-analysis by a previous run can never finish; say so."""

    for project in store.iter_projects():
        status = project.metadata.get("status")
        if status not in {"analyzing", "created"}:
            continue
        if not project.source_file.is_file():
            store.update_metadata(project, status="error", error="The upload did not finish. Delete this project and import the audio again.")
        elif project.cues_file.is_file():
            store.update_metadata(project, status="ready", error="The last re-analysis was interrupted; the previous timeline was kept.")
        else:
            store.update_metadata(project, status="error", error="Analysis was interrupted. Use Re-analyze to try again.")
