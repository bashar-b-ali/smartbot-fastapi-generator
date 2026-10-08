"""Project-scoped activity tracking for long-running local model jobs."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.pipeline_memory import PipelineMemoryRepository

_lock = asyncio.Lock()
_active: dict[str, dict[str, Any]] = {}
ACTIVITY_STALE_AFTER = timedelta(hours=2)


def _key(project_id: UUID | str) -> str:
    return str(project_id)


def _activity_source(_: str) -> str:
    return "activity"


def _activity_is_stale(activity: dict[str, Any]) -> bool:
    started = str(activity.get("started_at") or "")
    if not started:
        return False
    try:
        started_at = datetime.fromisoformat(started)
    except ValueError:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - started_at > ACTIVITY_STALE_AFTER


async def project_activity(project_id: UUID | str, db: AsyncSession | None = None) -> dict[str, Any]:
    async with _lock:
        activity = dict(_active.get(_key(project_id)) or {})
        if activity and _activity_is_stale(activity):
            _active.pop(_key(project_id), None)
            activity = {}
    if activity or db is None or not isinstance(project_id, UUID):
        return activity
    run = await PipelineMemoryRepository(db).active_run(project_id, stale_after=ACTIVITY_STALE_AFTER)
    if run is None:
        return {}
    stats = run.stats if isinstance(run.stats, dict) else {}
    return {
        "project_id": _key(project_id),
        "operation": run.source or "model_job",
        "job_id": str(stats.get("job_id") or run.id),
        "run_id": str(run.id),
        "message": str(stats.get("activity_message") or "Local model job is running."),
        "started_at": run.created_at.isoformat() if run.created_at else "",
        "durable": True,
    }


async def clear_orphaned_project_activity(db: AsyncSession) -> int:
    async with _lock:
        cleared_in_memory = len(_active)
        _active.clear()
    cleared_durable = await PipelineMemoryRepository(db).mark_orphaned_activity_runs_interrupted()
    return cleared_in_memory + cleared_durable


async def project_is_busy(project_id: UUID | str, db: AsyncSession | None = None) -> bool:
    return bool(await project_activity(project_id, db))


async def require_project_not_busy(
    project_id: UUID | str,
    *,
    action: str,
    db: AsyncSession | None = None,
) -> None:
    _ = (project_id, action, db)
    return None


async def start_project_activity(
    project_id: UUID | str,
    *,
    operation: str,
    job_id: str = "",
    message: str = "",
    db: AsyncSession | None = None,
) -> None:
    activity = {
        "project_id": _key(project_id),
        "operation": operation,
        "job_id": job_id,
        "message": message,
        "started_at": datetime.now(UTC).isoformat(),
    }
    if db is not None and isinstance(project_id, UUID):
        run = await PipelineMemoryRepository(db).start_run(
            project_id=project_id,
            source=_activity_source(operation),
            prompt=message,
            provider="activity",
            job_id=job_id,
            message=message,
        )
        activity["run_id"] = str(run.id)
        activity["started_at"] = run.created_at.isoformat() if run.created_at else ""
    async with _lock:
        _active[_key(project_id)] = activity


async def clear_project_activity(
    project_id: UUID | str,
    *,
    job_id: str = "",
    db: AsyncSession | None = None,
    status: str = "finished",
) -> None:
    run_id = ""
    async with _lock:
        current = _active.get(_key(project_id)) or {}
        if not job_id or current.get("job_id") == job_id:
            run_id = str(current.get("run_id") or "")
            _active.pop(_key(project_id), None)
    if db is None:
        return
    repo = PipelineMemoryRepository(db)
    if run_id:
        await repo.finish_run(UUID(run_id), status=status, accepted=status == "accepted")
        return
    if isinstance(project_id, UUID):
        run = await repo.active_run(project_id)
        if run is not None:
            await repo.finish_run(run.id, status=status, accepted=status == "accepted")


@asynccontextmanager
async def track_project_activity(
    project_id: UUID | str,
    *,
    operation: str,
    job_id: str = "",
    message: str = "",
    db: AsyncSession | None = None,
):
    await start_project_activity(project_id, operation=operation, job_id=job_id, message=message, db=db)
    try:
        yield
    finally:
        await clear_project_activity(project_id, job_id=job_id, db=db)
