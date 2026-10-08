from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.logging import logger
from app.db.session import SessionLocal
from app.models.project_run import ProjectAgentCheckpoint, ProjectAgentRun
from app.realtime.manager import manager as realtime_manager
from app.repositories.chat import ChatMessageRepository
from app.repositories.project_run import ProjectRunRepository
from app.repositories.user import UserRepository
from app.schemas.project_run import (
    CONTRACT_VERSION,
    ProjectRunCheckpointOut,
    ProjectRunEventOut,
    ProjectRunOut,
)
from app.services.chat import ChatService
from app.services.generator import GeneratorService
from app.services.project_editor import ProjectEditService

LEASE_DURATION = timedelta(seconds=45)
HEARTBEAT_SECONDS = 10
POLL_SECONDS = 1
WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"


def checkpoint_out(checkpoint: ProjectAgentCheckpoint) -> ProjectRunCheckpointOut:
    return ProjectRunCheckpointOut(
        id=checkpoint.id,
        sequence=checkpoint.sequence,
        title=checkpoint.title,
        status=checkpoint.status,
        stage=checkpoint.stage,
        attempts=checkpoint.attempts,
        plan=checkpoint.plan_json or {},
        result=checkpoint.result_json or {},
        error=checkpoint.error_json or {},
        changed_files=checkpoint.changed_files or [],
        base_revision=checkpoint.base_revision,
        applied_revision=checkpoint.applied_revision,
        started_at=checkpoint.started_at,
        completed_at=checkpoint.completed_at,
        created_at=checkpoint.created_at,
        updated_at=checkpoint.updated_at,
    )


async def run_out(repo: ProjectRunRepository, run: ProjectAgentRun) -> ProjectRunOut:
    checkpoints = [checkpoint_out(item) for item in await repo.checkpoints(run.id)]
    base = f"/api/v1/projects/{run.project_id}/runs/{run.id}"
    return ProjectRunOut(
        contract_version=run.contract_version or CONTRACT_VERSION,
        run_id=run.id,
        project_id=run.project_id,
        operation=run.operation,
        status=run.status,
        stage=run.stage,
        prompt=run.prompt,
        provider=run.provider,
        model_id=run.model_id,
        current_checkpoint=run.current_checkpoint,
        last_event_sequence=run.event_sequence,
        cancellation_requested=run.cancellation_requested,
        plan=run.plan_json or {},
        result=run.result_json or {},
        error=run.error_json or {},
        warnings=run.warnings_json or [],
        checkpoints=checkpoints,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        heartbeat_at=run.heartbeat_at,
        status_url=base,
        events_url=f"{base}/events",
    )


def event_out(event: Any) -> ProjectRunEventOut:
    return ProjectRunEventOut(
        sequence=event.sequence,
        type=event.event_type,
        project_id=event.project_id,
        run_id=event.run_id,
        payload=event.payload or {},
        created_at=event.created_at,
    )


async def publish_run_event(run: ProjectAgentRun, event_type: str, payload: dict[str, Any]) -> None:
    event = {
        "type": event_type,
        "contract_version": CONTRACT_VERSION,
        "sequence": run.event_sequence,
        "project_id": str(run.project_id),
        "run_id": str(run.id),
        "payload": payload,
    }
    await realtime_manager.publish(f"user:{run.user_id}:projects", event)
    await realtime_manager.publish(f"project:{run.project_id}:chat", event)


class ProjectRunWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop(), name="project-run-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            run_id: UUID | None = None
            try:
                async with SessionLocal() as db:
                    repo = ProjectRunRepository(db)
                    run = await repo.claim_next(WORKER_ID, lease_for=LEASE_DURATION)
                    if run is not None:
                        run_id = run.id
                    await db.commit()
                if run_id is None:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                await self._execute(run_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("project_run.worker_loop_failed", error=str(exc))
                await asyncio.sleep(POLL_SECONDS)

    async def _heartbeat(self, run_id: UUID) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with SessionLocal() as db:
                alive = await ProjectRunRepository(db).heartbeat(
                    run_id, WORKER_ID, lease_for=LEASE_DURATION
                )
                await db.commit()
            if not alive:
                return

    @staticmethod
    def _model_unavailable(exc: Exception) -> bool:
        message = f"{type(exc).__name__}: {exc}".lower()
        return isinstance(exc, (ConnectionError, OSError, TimeoutError)) or any(
            token in message
            for token in (
                "connection refused",
                "connection reset",
                "temporarily unavailable",
                "service unavailable",
                "timed out",
                "timeout",
                "ollama",
            )
        )

    async def _execute(self, run_id: UUID) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            async with SessionLocal() as db:
                repo = ProjectRunRepository(db)
                run = await db.get(ProjectAgentRun, run_id)
                if run is None or run.lease_owner != WORKER_ID:
                    return
                if run.cancellation_requested:
                    await self._cancel(repo, run)
                    await db.commit()
                    return
                user = await UserRepository(db).get_by_id(run.user_id)
                if user is None:
                    await self._fatal(repo, run, "user_not_found", "Run owner no longer exists.")
                    await db.commit()
                    return

                plan = {
                    "protocol_version": "work-plan.v1",
                    "operation": run.operation,
                    "summary": run.prompt,
                    "requirements": [{"id": "request", "description": run.prompt}],
                    "checkpoints": [
                        {
                            "sequence": 1,
                            "title": f"{run.operation.title()} requested project changes",
                            "status": "ready",
                        }
                    ],
                }
                run.plan_json = plan
                run.stage = "implementing"
                checkpoint = await repo.ensure_checkpoint(
                    run,
                    title=plan["checkpoints"][0]["title"],
                    plan=plan["checkpoints"][0],
                )
                checkpoint.status = "running"
                checkpoint.stage = "implementing"
                checkpoint.attempts += 1
                checkpoint.started_at = checkpoint.started_at or datetime.now(UTC)
                await repo.append_event(
                    run,
                    "project_run.checkpoint_started",
                    {
                        "status": run.status,
                        "stage": run.stage,
                        "checkpoint": checkpoint.sequence,
                        "title": checkpoint.title,
                    },
                )
                await db.commit()
                await publish_run_event(
                    run,
                    "project_run.checkpoint_started",
                    {"status": run.status, "stage": run.stage, "checkpoint": checkpoint.sequence},
                )

                request = run.request_json or {}
                if run.operation == "create":
                    result = await GeneratorService(db).generate_from_prompt(
                        run.project_id,
                        user,
                        prompt=run.prompt,
                        provider=str(request.get("provider") or "auto"),
                        model_id=run.model_id,
                        clean=False,
                    )
                else:
                    result = await ProjectEditService(db).edit_project(
                        run.project_id,
                        user,
                        prompt=run.prompt,
                        model_id=run.model_id,
                        provider_type=str(request.get("provider") or "auto"),
                    )

                await db.refresh(run)
                await db.refresh(checkpoint)
                if run.cancellation_requested:
                    await self._cancel(repo, run, checkpoint)
                else:
                    await self._finish_result(repo, run, checkpoint, result)
                await db.commit()
                await publish_run_event(
                    run,
                    "project_run.completed"
                    if run.status.startswith("completed")
                    else "project_run.needs_attention",
                    {
                        "status": run.status,
                        "stage": run.stage,
                        "checkpoint": checkpoint.sequence,
                        "changed_files": checkpoint.changed_files,
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_error(run_id, exc)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _finish_result(
        self,
        repo: ProjectRunRepository,
        run: ProjectAgentRun,
        checkpoint: ProjectAgentCheckpoint,
        result: dict[str, Any],
    ) -> None:
        files = result.get("files") if isinstance(result.get("files"), list) else []
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
        accepted = result.get("accepted") is True and validation.get("accepted") is True
        warnings = [
            *list(result.get("missing_artifacts") or []),
            *list(result.get("regressions") or []),
            *list(validation.get("warnings") or []),
        ]
        now = datetime.now(UTC)
        checkpoint.result_json = result
        checkpoint.changed_files = files
        checkpoint.completed_at = now
        run.result_json = {
            **result,
            "changed_files": files,
            "completed_requirements": ["request"] if accepted else [],
            "remaining_requirements": [] if accepted else ["request"],
        }
        run.warnings_json = warnings
        if accepted:
            edit_units = (
                (result.get("edit_plan") or {}).get("edit_units")
                if isinstance(result.get("edit_plan"), dict)
                else []
            )
            if isinstance(edit_units, list) and edit_units:
                for sequence, unit in enumerate(edit_units, start=1):
                    if not isinstance(unit, dict):
                        continue
                    unit_checkpoint = await repo.ensure_checkpoint(
                        run,
                        sequence=sequence,
                        title=f"Edit {unit.get('path') or f'unit {sequence}'}",
                        plan=unit,
                    )
                    unit_checkpoint.plan_json = unit
                    unit_checkpoint.result_json = {
                        "status": unit.get("status"),
                        "static_validation": unit.get("static_validation") or {},
                    }
                    unit_checkpoint.changed_files = [
                        {"path": path}
                        for path in unit.get("changed_files") or []
                        if isinstance(path, str)
                    ]
                    unit_checkpoint.attempts = max(1, int(unit_checkpoint.attempts or 0))
                    unit_checkpoint.started_at = unit_checkpoint.started_at or checkpoint.started_at
                    unit_checkpoint.completed_at = now
                    unit_checkpoint.status = "applied"
                    unit_checkpoint.stage = "applied"
                    await repo.append_event(
                        run,
                        "project_run.checkpoint_applied",
                        {
                            "status": "applied",
                            "stage": "applied",
                            "checkpoint": sequence,
                            "path": unit.get("path"),
                            "changed_files": unit_checkpoint.changed_files,
                        },
                    )
            else:
                checkpoint.status = "applied"
                checkpoint.stage = "applied"
            run.status = "completed_with_warnings" if warnings else "completed"
            run.stage = "finalizing"
            run.completed_at = now
            event_type = "project_run.checkpoint_applied"
        else:
            checkpoint.status = "blocked"
            checkpoint.stage = "needs_attention"
            checkpoint.error_json = {
                "code": str(result.get("failure_category") or "model_result_not_accepted"),
                "message": str(
                    (result.get("validation") or {}).get("errors")
                    or result.get("next_prompt")
                    or "The model did not produce an accepted change."
                ),
            }
            run.status = "needs_attention"
            run.stage = "needs_attention"
            run.error_json = checkpoint.error_json
            event_type = "project_run.checkpoint_blocked"
        await repo.release_lease(run)
        await repo.append_event(
            run,
            event_type,
            {
                "status": run.status,
                "stage": run.stage,
                "checkpoint": checkpoint.sequence,
                "changed_files": files,
                "warnings": warnings,
            },
        )
        assistant_message_id = str((run.request_json or {}).get("assistant_message_id") or "")
        if assistant_message_id:
            with contextlib.suppress(ValueError):
                message = await ChatMessageRepository(repo.db).get_by_id(UUID(assistant_message_id))
                if message is not None:
                    message = await ChatMessageRepository(repo.db).update(
                        message,
                        content=(
                            ChatService._generation_chat_message(result)
                            if accepted
                            else ChatService._generation_failure_message(
                                str(checkpoint.error_json.get("message") or "The checkpoint needs attention.")
                            )
                        ),
                        tokens_used=int((result.get("stats") or {}).get("input_tokens", 0) or 0)
                        + int((result.get("stats") or {}).get("output_tokens", 0) or 0),
                        model_used=f"project-run:{run.id}"[:50],
                    )
                    await ChatService._publish_chat_event(
                        run.project_id, "chat_message_updated", message
                    )

    async def _handle_error(self, run_id: UUID, exc: Exception) -> None:
        async with SessionLocal() as db:
            repo = ProjectRunRepository(db)
            run = await db.get(ProjectAgentRun, run_id)
            if run is None:
                return
            checkpoints = await repo.checkpoints(run.id)
            checkpoint = checkpoints[-1] if checkpoints else None
            error = {"code": type(exc).__name__, "message": str(exc)}
            if checkpoint is not None:
                checkpoint.error_json = error
                checkpoint.status = "retrying" if self._model_unavailable(exc) else "blocked"
                checkpoint.stage = (
                    "waiting_for_model" if self._model_unavailable(exc) else "needs_attention"
                )
            if self._model_unavailable(exc):
                attempts = checkpoint.attempts if checkpoint else 1
                run.status = "waiting_for_model"
                run.stage = "waiting_for_model"
                run.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(300, 5 * (2 ** min(attempts, 6)))
                )
                event_type = "project_run.waiting_for_model"
            else:
                run.status = "needs_attention"
                run.stage = "needs_attention"
                event_type = "project_run.needs_attention"
            run.error_json = error
            await repo.release_lease(run)
            await repo.append_event(
                run,
                event_type,
                {"status": run.status, "stage": run.stage, "error": error},
            )
            await db.commit()
            await publish_run_event(run, event_type, {"status": run.status, "error": error})

    async def _cancel(
        self,
        repo: ProjectRunRepository,
        run: ProjectAgentRun,
        checkpoint: ProjectAgentCheckpoint | None = None,
    ) -> None:
        now = datetime.now(UTC)
        run.status = "cancelled"
        run.stage = "cancelled"
        run.completed_at = now
        if checkpoint is not None and checkpoint.status != "applied":
            checkpoint.status = "cancelled"
            checkpoint.stage = "cancelled"
            checkpoint.completed_at = now
        await repo.release_lease(run)
        await repo.append_event(run, "project_run.cancelled", {"status": "cancelled"})

    async def _fatal(
        self,
        repo: ProjectRunRepository,
        run: ProjectAgentRun,
        code: str,
        message: str,
    ) -> None:
        run.status = "failed"
        run.stage = "failed"
        run.error_json = {"code": code, "message": message}
        run.completed_at = datetime.now(UTC)
        await repo.release_lease(run)
        await repo.append_event(run, "project_run.failed", {"error": run.error_json})


project_run_worker = ProjectRunWorker()
