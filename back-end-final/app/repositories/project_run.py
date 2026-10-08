from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_run import ProjectAgentCheckpoint, ProjectAgentEvent, ProjectAgentRun

ACTIVE_STATUSES = (
    "queued",
    "running",
    "waiting_for_model",
    "needs_input",
    "needs_attention",
)
TERMINAL_STATUSES = ("completed", "completed_with_warnings", "cancelled", "failed")


class ProjectRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        project_id: UUID,
        user_id: UUID,
        operation: str,
        prompt: str,
        provider: str,
        model_id: UUID | None,
        idempotency_key: str | None,
        request_json: dict[str, Any],
    ) -> ProjectAgentRun:
        if idempotency_key:
            existing = await self.db.scalar(
                select(ProjectAgentRun).where(
                    ProjectAgentRun.project_id == project_id,
                    ProjectAgentRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
        run = ProjectAgentRun(
            project_id=project_id,
            user_id=user_id,
            operation=operation,
            prompt=prompt,
            provider=provider,
            model_id=model_id,
            idempotency_key=idempotency_key,
            request_json=request_json,
            status="queued",
            stage="queued",
        )
        self.db.add(run)
        await self.db.flush()
        await self.append_event(run, "project_run.queued", {"status": "queued", "stage": "queued"})
        return run

    async def get(self, project_id: UUID, run_id: UUID) -> ProjectAgentRun | None:
        return await self.db.scalar(
            select(ProjectAgentRun).where(
                ProjectAgentRun.project_id == project_id,
                ProjectAgentRun.id == run_id,
            )
        )

    async def list(
        self,
        project_id: UUID,
        *,
        active_only: bool = False,
        limit: int = 30,
    ) -> list[ProjectAgentRun]:
        statement = select(ProjectAgentRun).where(ProjectAgentRun.project_id == project_id)
        if active_only:
            statement = statement.where(ProjectAgentRun.status.in_(ACTIVE_STATUSES))
        result = await self.db.execute(
            statement.order_by(ProjectAgentRun.created_at.desc()).limit(max(1, min(limit, 100)))
        )
        return list(result.scalars())

    async def checkpoints(self, run_id: UUID) -> list[ProjectAgentCheckpoint]:
        result = await self.db.execute(
            select(ProjectAgentCheckpoint)
            .where(ProjectAgentCheckpoint.run_id == run_id)
            .order_by(ProjectAgentCheckpoint.sequence)
        )
        return list(result.scalars())

    async def events(self, run_id: UUID, *, after: int = 0, limit: int = 200) -> list[ProjectAgentEvent]:
        result = await self.db.execute(
            select(ProjectAgentEvent)
            .where(ProjectAgentEvent.run_id == run_id, ProjectAgentEvent.sequence > max(0, after))
            .order_by(ProjectAgentEvent.sequence)
            .limit(max(1, min(limit, 500)))
        )
        return list(result.scalars())

    async def append_event(
        self,
        run: ProjectAgentRun,
        event_type: str,
        payload: dict[str, Any],
    ) -> ProjectAgentEvent:
        run.event_sequence = int(run.event_sequence or 0) + 1
        event = ProjectAgentEvent(
            run_id=run.id,
            project_id=run.project_id,
            sequence=run.event_sequence,
            event_type=event_type,
            payload=payload,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def ensure_checkpoint(
        self,
        run: ProjectAgentRun,
        *,
        title: str,
        plan: dict[str, Any],
        sequence: int = 1,
    ) -> ProjectAgentCheckpoint:
        sequence = max(1, sequence)
        checkpoint = await self.db.scalar(
            select(ProjectAgentCheckpoint).where(
                ProjectAgentCheckpoint.run_id == run.id,
                ProjectAgentCheckpoint.sequence == sequence,
            )
        )
        if checkpoint is not None:
            return checkpoint
        checkpoint = ProjectAgentCheckpoint(
            run_id=run.id,
            sequence=sequence,
            title=title[:255],
            plan_json=plan,
            status="ready",
            stage="ready",
        )
        self.db.add(checkpoint)
        run.current_checkpoint = max(int(run.current_checkpoint or 0), sequence)
        await self.db.flush()
        return checkpoint

    async def claim_next(self, worker_id: str, *, lease_for: timedelta) -> ProjectAgentRun | None:
        now = datetime.now(UTC)
        candidate_query = (
            select(ProjectAgentRun)
            .where(
                or_(
                    ProjectAgentRun.status == "queued",
                    ProjectAgentRun.status == "waiting_for_model",
                    (
                        (ProjectAgentRun.status == "running")
                        & (ProjectAgentRun.lease_expires_at.is_not(None))
                        & (ProjectAgentRun.lease_expires_at < now)
                    ),
                ),
                or_(ProjectAgentRun.next_attempt_at.is_(None), ProjectAgentRun.next_attempt_at <= now),
            )
            .order_by(ProjectAgentRun.created_at)
            .limit(20)
        )
        if self.db.get_bind().dialect.name != "sqlite":
            candidate_query = candidate_query.with_for_update()
        candidates = list(
            (
                await self.db.execute(candidate_query)
            ).scalars()
        )
        for run in candidates:
            busy = await self.db.scalar(
                select(ProjectAgentRun.id)
                .where(
                    ProjectAgentRun.project_id == run.project_id,
                    ProjectAgentRun.id != run.id,
                    ProjectAgentRun.status == "running",
                    ProjectAgentRun.lease_expires_at >= now,
                )
                .limit(1)
            )
            if busy is not None:
                continue
            run.status = "running"
            run.stage = "planning" if not run.started_at else run.stage
            run.lease_owner = worker_id
            run.lease_expires_at = now + lease_for
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            await self.append_event(
                run,
                "project_run.started" if not run.current_checkpoint else "project_run.resumed",
                {"status": run.status, "stage": run.stage},
            )
            await self.db.flush()
            return run
        return None

    async def heartbeat(self, run_id: UUID, worker_id: str, *, lease_for: timedelta) -> bool:
        run = await self.db.get(ProjectAgentRun, run_id)
        if run is None or run.lease_owner != worker_id or run.status != "running":
            return False
        now = datetime.now(UTC)
        run.heartbeat_at = now
        run.lease_expires_at = now + lease_for
        await self.db.flush()
        return True

    async def release_lease(self, run: ProjectAgentRun) -> None:
        run.lease_owner = ""
        run.lease_expires_at = None
        await self.db.flush()
