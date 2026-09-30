from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_PART = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    """Return a portable basename without permitting directory traversal."""

    basename = Path(str(filename).replace("\\", "/")).name.strip()
    if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", basename):
        stem, suffix = "audio", basename.lower()
    else:
        path = Path(basename)
        suffix = path.suffix.lower()
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
            suffix = ""
        stem = basename[: -len(path.suffix)] if path.suffix else basename
        stem = _SAFE_PART.sub("_", stem).strip("._-")
        stem = re.sub(r"_+", "_", stem)
        if not stem:
            stem = "audio"
    return f"{stem[:120]}{suffix}"


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: str
    root: Path
    source_file: Path
    metadata: dict[str, Any]

    @property
    def analysis_file(self) -> Path:
        return self.root / "analysis.wav"

    @property
    def cues_file(self) -> Path:
        return self.root / "cues.json"

    @property
    def waveform_file(self) -> Path:
        return self.root / "waveform.json"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def transcript_file(self) -> Path:
        return self.root / "transcript.txt"

    @property
    def display_name(self) -> str:
        return str(self.metadata.get("original_name") or self.source_file.name)


class ProjectStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._metadata_lock = threading.Lock()

    @staticmethod
    def _validate_id(project_id: str) -> str:
        if not _PROJECT_ID.fullmatch(project_id):
            raise ValueError("invalid project id")
        return project_id

    def create_project(
        self,
        original_name: str,
        *,
        settings: dict[str, Any] | None = None,
    ) -> ProjectRecord:
        project_id = uuid.uuid4().hex
        project_root = self.root / project_id
        source_dir = project_root / "source"
        exports_dir = project_root / "exports"
        source_dir.mkdir(parents=True, exist_ok=False)
        exports_dir.mkdir(parents=True, exist_ok=True)
        stored_name = sanitize_filename(original_name)
        metadata: dict[str, Any] = {
            "id": project_id,
            "original_name": Path(str(original_name).replace("\\", "/")).name,
            "stored_name": stored_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": settings or {},
            "status": "created",
        }
        record = ProjectRecord(
            id=project_id,
            root=project_root,
            source_file=source_dir / stored_name,
            metadata=metadata,
        )
        self.write_json(record, "project.json", metadata)
        return record

    def load_project(self, project_id: str) -> ProjectRecord:
        project_id = self._validate_id(project_id)
        project_root = (self.root / project_id).resolve()
        if project_root.parent != self.root or not project_root.is_dir():
            raise FileNotFoundError(f"project {project_id} was not found")
        metadata_path = project_root / "project.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"project {project_id} is incomplete") from exc
        stored_name = sanitize_filename(str(metadata.get("stored_name", "audio")))
        return ProjectRecord(
            id=project_id,
            root=project_root,
            source_file=project_root / "source" / stored_name,
            metadata=metadata,
        )

    def iter_projects(self) -> list[ProjectRecord]:
        records: list[ProjectRecord] = []
        for candidate in self.root.iterdir():
            if not candidate.is_dir() or not _PROJECT_ID.fullmatch(candidate.name):
                continue
            try:
                records.append(self.load_project(candidate.name))
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                continue
        records.sort(
            key=lambda record: str(record.metadata.get("created_at", "")),
            reverse=True,
        )
        return records

    def list_projects(self, limit: int = 20) -> list[ProjectRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return self.iter_projects()[:limit]

    def delete_project(self, project: ProjectRecord) -> None:
        root = project.root.resolve()
        if root.parent != self.root or not _PROJECT_ID.fullmatch(root.name):
            raise ValueError("refusing to delete outside the project store")
        shutil.rmtree(root)

    def list_exports(self, project: ProjectRecord) -> list[Path]:
        if not project.exports_dir.is_dir():
            return []
        files = [
            path
            for path in project.exports_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".mp4" and not path.name.startswith(".")
        ]
        return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)

    def resolve_relative(self, project: ProjectRecord, relative_path: str | Path) -> Path:
        candidate = (project.root / Path(relative_path)).resolve()
        if not candidate.is_relative_to(project.root.resolve()):
            raise ValueError("path must remain inside project")
        return candidate

    def write_json(self, project: ProjectRecord, relative_path: str | Path, value: Any) -> Path:
        target = self.resolve_relative(project, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def read_json(self, project: ProjectRecord, relative_path: str | Path) -> Any:
        target = self.resolve_relative(project, relative_path)
        return json.loads(target.read_text(encoding="utf-8"))

    def update_metadata(self, project: ProjectRecord, **updates: Any) -> ProjectRecord:
        """Merge *updates* into project.json, re-reading it so concurrent edits survive."""

        with self._metadata_lock:
            try:
                metadata = self.read_json(project, "project.json")
            except (FileNotFoundError, json.JSONDecodeError):
                metadata = dict(project.metadata)
            metadata.update(updates)
            self.write_json(project, "project.json", metadata)
        return ProjectRecord(project.id, project.root, project.source_file, metadata)
