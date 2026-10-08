from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_memory import (
    ProjectApiContractVersion,
    ProjectBlueprint,
    ProjectFilePlan,
    ProjectFileSummary,
    ProjectPipelineRun,
    ProjectPipelineTraceEvent,
    ProjectSchemaVersion,
)
from app.repositories.base import BaseRepository


class ProjectBlueprintRepository(BaseRepository[ProjectBlueprint]):
    model = ProjectBlueprint


class ProjectSchemaVersionRepository(BaseRepository[ProjectSchemaVersion]):
    model = ProjectSchemaVersion


class ProjectApiContractVersionRepository(BaseRepository[ProjectApiContractVersion]):
    model = ProjectApiContractVersion


class ProjectFilePlanRepository(BaseRepository[ProjectFilePlan]):
    model = ProjectFilePlan


class ProjectPipelineRunRepository(BaseRepository[ProjectPipelineRun]):
    model = ProjectPipelineRun


class ProjectPipelineTraceEventRepository(BaseRepository[ProjectPipelineTraceEvent]):
    model = ProjectPipelineTraceEvent


class ProjectFileSummaryRepository(BaseRepository[ProjectFileSummary]):
    model = ProjectFileSummary


class PipelineMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def start_run(
        self,
        *,
        project_id: UUID,
        source: str,
        prompt: str,
        provider: str = "",
        job_id: str = "",
        message: str = "",
    ) -> ProjectPipelineRun:
        run = ProjectPipelineRun(
            project_id=project_id,
            source=source[:20],
            provider=provider[:255],
            status="running",
            prompt=prompt,
            stats={
                "job_id": job_id,
                "activity_message": message,
                "started_at": datetime.now(UTC).isoformat(),
            },
            stage_outputs={},
            accepted=False,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        accepted: bool = False,
        provider: str | None = None,
        stats: dict[str, Any] | None = None,
        stage_outputs: dict[str, Any] | None = None,
    ) -> None:
        result = await self.db.execute(select(ProjectPipelineRun).where(ProjectPipelineRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            return
        run.status = status[:30]
        run.accepted = accepted
        if provider is not None:
            run.provider = provider[:255]
        if stats is not None:
            run.stats = stats
        if stage_outputs is not None:
            run.stage_outputs = stage_outputs
        await self.db.flush()

    async def mark_orphaned_activity_runs_interrupted(self) -> int:
        result = await self.db.execute(
            update(ProjectPipelineRun)
            .where(
                ProjectPipelineRun.provider == "activity",
                ProjectPipelineRun.status.in_(("queued", "started", "running")),
            )
            .values(status="interrupted", accepted=False)
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def active_run(
        self,
        project_id: UUID,
        *,
        stale_after: timedelta = timedelta(hours=6),
    ) -> ProjectPipelineRun | None:
        cutoff = datetime.now(UTC) - stale_after
        result = await self.db.execute(
            select(ProjectPipelineRun)
            .where(
                ProjectPipelineRun.project_id == project_id,
                ProjectPipelineRun.status.in_(("queued", "started", "running")),
                ProjectPipelineRun.created_at >= cutoff,
            )
            .order_by(ProjectPipelineRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _next_version(self, model: type[Any], project_id: UUID) -> int:
        result = await self.db.execute(
            select(func.max(model.version)).where(model.project_id == project_id)
        )
        return int(result.scalar() or 0) + 1

    async def latest_schema(self, project_id: UUID) -> ProjectSchemaVersion | None:
        result = await self.db.execute(
            select(ProjectSchemaVersion)
            .where(ProjectSchemaVersion.project_id == project_id, ProjectSchemaVersion.accepted.is_(True))
            .order_by(ProjectSchemaVersion.version.desc(), ProjectSchemaVersion.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_api_contract(self, project_id: UUID) -> ProjectApiContractVersion | None:
        result = await self.db.execute(
            select(ProjectApiContractVersion)
            .where(ProjectApiContractVersion.project_id == project_id, ProjectApiContractVersion.accepted.is_(True))
            .order_by(ProjectApiContractVersion.version.desc(), ProjectApiContractVersion.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_file_plan(self, project_id: UUID) -> ProjectFilePlan | None:
        result = await self.db.execute(
            select(ProjectFilePlan)
            .where(ProjectFilePlan.project_id == project_id, ProjectFilePlan.accepted.is_(True))
            .order_by(ProjectFilePlan.version.desc(), ProjectFilePlan.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def compact_context(self, project_id: UUID) -> dict[str, Any]:
        schema = await self.latest_schema(project_id)
        api = await self.latest_api_contract(project_id)
        file_plan = await self.latest_file_plan(project_id)
        return {
            "schema": {
                "ddl_sql": schema.ddl_sql if schema else "",
                "schema": schema.schema_json if schema else {},
                "version": schema.version if schema else None,
            },
            "api_contract": {
                "contract": api.contract_json if api else {},
                "artifact_contract": api.artifact_contract if api else {},
                "version": api.version if api else None,
            },
            "file_plan": {
                "plan": file_plan.file_plan if file_plan else {},
                "version": file_plan.version if file_plan else None,
            },
        }

    async def list_runs(self, project_id: UUID, *, limit: int = 20, offset: int = 0) -> list[ProjectPipelineRun]:
        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)
        result = await self.db.execute(
            select(ProjectPipelineRun)
            .where(ProjectPipelineRun.project_id == project_id)
            .order_by(ProjectPipelineRun.created_at.desc())
            .limit(bounded_limit)
            .offset(bounded_offset)
        )
        return list(result.scalars().all())

    async def get_run(self, project_id: UUID, run_id: UUID) -> ProjectPipelineRun | None:
        result = await self.db.execute(
            select(ProjectPipelineRun).where(
                ProjectPipelineRun.project_id == project_id,
                ProjectPipelineRun.id == run_id,
            )
        )
        return result.scalar_one_or_none()

    async def trace_events(self, project_id: UUID, run_id: UUID) -> list[ProjectPipelineTraceEvent]:
        result = await self.db.execute(
            select(ProjectPipelineTraceEvent)
            .where(
                ProjectPipelineTraceEvent.project_id == project_id,
                ProjectPipelineTraceEvent.run_id == run_id,
            )
            .order_by(ProjectPipelineTraceEvent.created_at.asc(), ProjectPipelineTraceEvent.id.asc())
        )
        return list(result.scalars().all())

    async def save_generation_memory(
        self,
        *,
        project_id: UUID,
        prompt: str,
        provider: str,
        accepted: bool,
        plan: dict[str, Any],
        stage_outputs: dict[str, Any],
        stats: dict[str, Any],
    ) -> ProjectPipelineRun:
        schema_stage = stage_outputs.get("schema_plan") if isinstance(stage_outputs, dict) else {}
        api_stage = stage_outputs.get("api_contract") if isinstance(stage_outputs, dict) else {}
        file_stage = stage_outputs.get("file_plan") if isinstance(stage_outputs, dict) else {}
        trace = stage_outputs.get("trace") if isinstance(stage_outputs, dict) else []

        blueprint = ProjectBlueprint(
            project_id=project_id,
            source="generation",
            prompt=prompt,
            summary=str(plan.get("description") or plan.get("project_name") or "")[:4000],
            domain=str((schema_stage or {}).get("domain") or plan.get("project_name") or "")[:255],
            accepted=accepted,
        )
        self.db.add(blueprint)
        await self.db.flush()

        schema_version: ProjectSchemaVersion | None = None
        if isinstance(schema_stage, dict):
            schema_version = ProjectSchemaVersion(
                project_id=project_id,
                blueprint_id=blueprint.id,
                version=await self._next_version(ProjectSchemaVersion, project_id),
                source="generation",
                ddl_sql=str(schema_stage.get("ddl_sql") or ""),
                schema_json=schema_stage.get("schema") if isinstance(schema_stage.get("schema"), dict) else {},
                validation=schema_stage.get("validation") if isinstance(schema_stage.get("validation"), dict) else {},
                accepted=accepted and bool((schema_stage.get("validation") or {}).get("passed", True)),
            )
            self.db.add(schema_version)
            await self.db.flush()

        api_version: ProjectApiContractVersion | None = None
        if isinstance(api_stage, dict):
            api_version = ProjectApiContractVersion(
                project_id=project_id,
                schema_version_id=schema_version.id if schema_version else None,
                version=await self._next_version(ProjectApiContractVersion, project_id),
                source="generation",
                contract_json=api_stage,
                artifact_contract=api_stage.get("artifact_contract") if isinstance(api_stage.get("artifact_contract"), dict) else {},
                validation=api_stage.get("validation") if isinstance(api_stage.get("validation"), dict) else {},
                accepted=accepted,
            )
            self.db.add(api_version)
            await self.db.flush()

        if isinstance(file_stage, dict):
            self.db.add(
                ProjectFilePlan(
                    project_id=project_id,
                    api_contract_id=api_version.id if api_version else None,
                    version=await self._next_version(ProjectFilePlan, project_id),
                    source="generation",
                    file_plan=file_stage,
                    accepted=accepted,
                )
            )

        run = ProjectPipelineRun(
            project_id=project_id,
            source="generation",
            provider=provider,
            status="accepted" if accepted else "rejected",
            prompt=prompt,
            stats=stats,
            stage_outputs=stage_outputs,
            accepted=accepted,
        )
        self.db.add(run)
        await self.db.flush()

        if isinstance(trace, list):
            for event in trace[:80]:
                if not isinstance(event, dict):
                    continue
                self.db.add(
                    ProjectPipelineTraceEvent(
                        project_id=project_id,
                        run_id=run.id,
                        stage=str(event.get("stage") or "")[:120],
                        status=str(event.get("status") or "")[:40],
                        event=event,
                    )
                )

        await self.db.flush()
        return run

    async def save_edit_memory(
        self,
        *,
        project_id: UUID,
        prompt: str,
        provider: str,
        accepted: bool,
        edit_plan: dict[str, Any],
        stage_outputs: dict[str, Any],
        stats: dict[str, Any],
    ) -> ProjectPipelineRun:
        schema_delta = stage_outputs.get("schema_delta") if isinstance(stage_outputs, dict) else {}
        api_delta = stage_outputs.get("api_delta") if isinstance(stage_outputs, dict) else {}
        trace = stage_outputs.get("trace") if isinstance(stage_outputs, dict) else []

        latest_schema = await self.latest_schema(project_id)
        if isinstance(schema_delta, dict) and accepted and schema_delta.get("schema"):
            self.db.add(
                ProjectSchemaVersion(
                    project_id=project_id,
                    blueprint_id=None,
                    version=await self._next_version(ProjectSchemaVersion, project_id),
                    source="edit",
                    ddl_sql=str(schema_delta.get("ddl_sql") or (latest_schema.ddl_sql if latest_schema else "")),
                    schema_json=schema_delta.get("schema") if isinstance(schema_delta.get("schema"), dict) else {},
                    validation=schema_delta.get("validation") if isinstance(schema_delta.get("validation"), dict) else {},
                    accepted=True,
                )
            )

        if isinstance(api_delta, dict) and accepted:
            self.db.add(
                ProjectApiContractVersion(
                    project_id=project_id,
                    schema_version_id=None,
                    version=await self._next_version(ProjectApiContractVersion, project_id),
                    source="edit",
                    contract_json=api_delta,
                    artifact_contract=api_delta.get("artifact_contract") if isinstance(api_delta.get("artifact_contract"), dict) else {},
                    validation=api_delta.get("validation") if isinstance(api_delta.get("validation"), dict) else {},
                    accepted=True,
                )
            )

        run = ProjectPipelineRun(
            project_id=project_id,
            source="edit",
            provider=provider,
            status="accepted" if accepted else "rejected",
            prompt=prompt,
            stats=stats,
            stage_outputs=stage_outputs,
            accepted=accepted,
        )
        self.db.add(run)
        await self.db.flush()

        if isinstance(trace, list):
            for event in trace[:80]:
                if not isinstance(event, dict):
                    continue
                self.db.add(
                    ProjectPipelineTraceEvent(
                        project_id=project_id,
                        run_id=run.id,
                        stage=str(event.get("stage") or "")[:120],
                        status=str(event.get("status") or "")[:40],
                        event=event,
                    )
                )
        await self.db.flush()
        return run
