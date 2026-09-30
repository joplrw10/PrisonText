from __future__ import annotations

from pumpkinsync.jobs import Job, JobRegistry


def test_find_active_filters_by_kind_and_options() -> None:
    registry = JobRegistry()
    preview = registry.add(Job(kind="export", project_id="p1", options={"quality": "preview"}))
    registry.add(Job(kind="analyze", project_id="p1", status="complete"))

    assert registry.find_active("p1", "export", quality="preview") is preview
    assert registry.find_active("p1", "export", quality="full") is None
    assert registry.find_active("p1", "analyze") is None
    assert registry.find_active("p2") is None

    registry.update(preview.id, status="complete")
    assert registry.find_active("p1") is None


def test_registry_prunes_old_finished_jobs_but_keeps_active_ones() -> None:
    registry = JobRegistry(keep=3)
    running = registry.add(Job(kind="export", project_id="p", status="running", created_at=0.0))
    finished = [registry.add(Job(kind="export", project_id="p", status="complete", created_at=float(i + 1))) for i in range(5)]

    assert registry.get(running.id) is running
    assert registry.get(finished[0].id) is None
    assert registry.get(finished[-1].id) is finished[-1]
    assert running.public_dict()["job_url"] == f"/api/jobs/{running.id}"
