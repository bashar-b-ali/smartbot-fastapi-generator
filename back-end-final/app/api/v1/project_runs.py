from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, get_current_user
from app.core.exceptions import NotFoundError, ValidationError
from app.models.project_run import ProjectAgentRun
from app.repositories.project import ProjectRepository
from app.repositories.project_run import (
    TERMINAL_STATUSES,
    ProjectRunRepository,
)
from app.schemas.project_run import (
    ProjectRunControl,
    ProjectRunCreate,
    ProjectRunEventsOut,
    ProjectRunOut,
)
from app.services.project_runs import event_out, publish_run_event, run_out

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["project-runs"])


async def _project_for_user(db: DbSession, project_id: UUID, user) -> None:
    if await ProjectRepository(db).get_for_user(project_id, user.id) is None:
        raise NotFoundError("Project not found")


async def _owned_run(
    db: DbSession,
    project_id: UUID,
    run_id: UUID,
    user,
) -> tuple[ProjectRunRepository, ProjectAgentRun]:
    await _project_for_user(db, project_id, user)
    repo = ProjectRunRepository(db)
    run = await repo.get(project_id, run_id)
    if run is None or run.user_id != user.id:
        raise NotFoundError("Project run not found")
    return repo, run


@router.post("", response_model=ProjectRunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_project_run(
    project_id: UUID,
    payload: ProjectRunCreate,
    db: DbSession,
    user=Depends(get_current_user),
) -> ProjectRunOut:
    await _project_for_user(db, project_id, user)
    repo = ProjectRunRepository(db)
    run = await repo.create(
        project_id=project_id,
        user_id=user.id,
        operation=payload.operation,
        prompt=payload.prompt,
        provider=payload.provider,
        model_id=payload.model_id,
        idempotency_key=payload.idempotency_key,
        request_json=payload.model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(run)
    await publish_run_event(run, "project_run.queued", {"status": run.status, "stage": run.stage})
    return await run_out(repo, run)


@router.get("", response_model=list[ProjectRunOut])
async def list_project_runs(
    project_id: UUID,
    db: DbSession,
    active: bool = Query(default=False),
    limit: int = Query(default=30, ge=1, le=100),
    user=Depends(get_current_user),
) -> list[ProjectRunOut]:
    await _project_for_user(db, project_id, user)
    repo = ProjectRunRepository(db)
    return [await run_out(repo, item) for item in await repo.list(project_id, active_only=active, limit=limit)]


@router.get("/{run_id}", response_model=ProjectRunOut)
async def get_project_run(
    project_id: UUID,
    run_id: UUID,
    db: DbSession,
    user=Depends(get_current_user),
) -> ProjectRunOut:
    repo, run = await _owned_run(db, project_id, run_id, user)
    return await run_out(repo, run)


@router.get("/{run_id}/events", response_model=ProjectRunEventsOut)
async def get_project_run_events(
    project_id: UUID,
    run_id: UUID,
    db: DbSession,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user=Depends(get_current_user),
) -> ProjectRunEventsOut:
    repo, run = await _owned_run(db, project_id, run_id, user)
    events = await repo.events(run.id, after=after, limit=limit)
    return ProjectRunEventsOut(
        run_id=run.id,
        after=after,
        last_sequence=events[-1].sequence if events else run.event_sequence,
        events=[event_out(item) for item in events],
    )


@router.post("/{run_id}/retry", response_model=ProjectRunOut, status_code=status.HTTP_202_ACCEPTED)
@router.post("/{run_id}/resume", response_model=ProjectRunOut, status_code=status.HTTP_202_ACCEPTED)
async def resume_project_run(
    project_id: UUID,
    run_id: UUID,
    payload: ProjectRunControl,
    db: DbSession,
    user=Depends(get_current_user),
) -> ProjectRunOut:
    repo, run = await _owned_run(db, project_id, run_id, user)
    if run.status in TERMINAL_STATUSES:
        raise ValidationError("A terminal run cannot be resumed")
    if payload.instruction.strip():
        request = dict(run.request_json or {})
        instructions = list(request.get("resume_instructions") or [])
        instructions.append(payload.instruction.strip())
        request["resume_instructions"] = instructions
        run.request_json = request
        run.prompt = f"{run.prompt}\n\nAdditional instruction: {payload.instruction.strip()}"
    if payload.model_id:
        run.model_id = payload.model_id
    run.status = "queued"
    run.stage = "queued"
    run.error_json = {}
    run.next_attempt_at = None
    run.cancellation_requested = False
    await repo.release_lease(run)
    await repo.append_event(run, "project_run.resume_requested", {"status": "queued"})
    await db.commit()
    await publish_run_event(run, "project_run.resume_requested", {"status": "queued"})
    return await run_out(repo, run)


@router.post("/{run_id}/cancel", response_model=ProjectRunOut, status_code=status.HTTP_202_ACCEPTED)
async def cancel_project_run(
    project_id: UUID,
    run_id: UUID,
    db: DbSession,
    user=Depends(get_current_user),
) -> ProjectRunOut:
    repo, run = await _owned_run(db, project_id, run_id, user)
    if run.status in TERMINAL_STATUSES:
        return await run_out(repo, run)
    run.cancellation_requested = True
    if run.status != "running":
        run.status = "cancelled"
        run.stage = "cancelled"
        run.completed_at = datetime.now(UTC)
        await repo.release_lease(run)
    await repo.append_event(
        run,
        "project_run.cancel_requested",
        {"status": run.status, "cancellation_requested": True},
    )
    await db.commit()
    await publish_run_event(
        run,
        "project_run.cancel_requested",
        {"status": run.status, "cancellation_requested": True},
    )
    return await run_out(repo, run)
