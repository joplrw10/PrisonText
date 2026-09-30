from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

ACTIVE_STATES = frozenset({"queued", "running"})


@dataclass(slots=True)
class Job:
    kind: str
    project_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    progress: float = 0.0
    message: str = "Waiting to start"
    error: str | None = None
    result: dict[str, Any] | None = None
    options: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATES

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["progress"] = round(float(self.progress), 4)
        payload["job_url"] = f"/api/jobs/{self.id}"
        return payload


class JobRegistry:
    """Thread-safe in-memory record of analysis and export work."""

    def __init__(self, keep: int = 200) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._keep = keep

    def add(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            self._prune()
            return job

    def _prune(self) -> None:
        finished = [job for job in self._jobs.values() if not job.active]
        overflow = len(self._jobs) - self._keep
        for job in sorted(finished, key=lambda item: item.created_at)[: max(0, overflow)]:
            del self._jobs[job.id]

    def update(self, job_id: str, **updates: Any) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in updates.items():
                setattr(job, key, value)
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_active(self, project_id: str, kind: str | None = None, **options: Any) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.project_id != project_id or not job.active:
                    continue
                if kind is not None and job.kind != kind:
                    continue
                if any(job.options.get(key) != value for key, value in options.items()):
                    continue
                return job
        return None

    def for_project(self, project_id: str) -> list[Job]:
        with self._lock:
            return sorted(
                (job for job in self._jobs.values() if job.project_id == project_id),
                key=lambda item: item.created_at,
            )
