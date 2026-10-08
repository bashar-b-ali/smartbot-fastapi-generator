import mimetypes
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DbSession, get_current_user
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.pipeline_memory import ProjectPipelineRun
from app.models.user import User
from app.repositories.chat import ProjectHelperRepository
from app.repositories.project_run import ProjectRunRepository
from app.schemas.project_run import ProjectRunControl, ProjectRunOut
from app.schemas.project import (
    FileContent,
    FolderEntry,
    ProjectCreate,
    ProjectIndexOut,
    ProjectOut,
    ProjectStats,
    ProjectUpdate,
)
from app.services import project_files as fs
from app.services.project_activity import project_activity
from app.services.project_indexer import ProjectIndexer
from app.services.project_runs import publish_run_event, run_out
from app.services.projects import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])

CurrentUser = Annotated[User, Depends(get_current_user)]

def _empty_pipeline_cluster() -> dict:
    return {
        "runs": 0,
        "accepted": 0,
        "failed": 0,
        "acceptance_rate": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "avg_runtime_ms": 0.0,
        "avg_changed_files": 0.0,
        "latest_status": "",
        "latest_provider": "",
    }


def _pipeline_cluster_key(source: str) -> str:
    value = (source or "").lower()
    if value in {"generation", "create", "creation"}:
        return "creation"
    if value in {"edit", "editing", "patch"}:
        return "edit"
    return value or "unknown"


def _runtime_ms(stats: dict, stage_outputs: dict) -> int:
    for key in ("duration_ms", "runtime_ms", "elapsed_ms", "total_duration_ms"):
        if isinstance(stats.get(key), int | float):
            return int(stats[key])
    timings = stage_outputs.get("stage_timings_ms") if isinstance(stage_outputs, dict) else {}
    if isinstance(timings, dict):
        return int(sum(value for value in timings.values() if isinstance(value, int | float)))
    return 0


def _changed_file_count(stats: dict, stage_outputs: dict) -> int:
    for key in ("changed_files", "candidate_files", "rendered_paths"):
        value = stats.get(key) if isinstance(stats, dict) else None
        if isinstance(value, list):
            return len(value)
    if isinstance(stage_outputs, dict):
        for key in ("changed_files", "candidate_files", "rendered_paths", "file_summaries"):
            value = stage_outputs.get(key)
            if isinstance(value, list):
                return len(value)
    return 0



async def _project_out(project, db: DbSession) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    activity = await project_activity(project.id, db)
    out.llm_activity = activity
    out.llm_busy = bool(activity)
    return out
async def _edit_creation_cluster_metrics(project_id: UUID, db: DbSession) -> dict[str, dict]:
    rows = list(
        (
            await db.execute(
                select(ProjectPipelineRun)
                .where(ProjectPipelineRun.project_id == project_id, ProjectPipelineRun.provider != "activity")
                .order_by(ProjectPipelineRun.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    clusters = {"creation": _empty_pipeline_cluster(), "edit": _empty_pipeline_cluster()}
    runtime_totals: dict[str, int] = {"creation": 0, "edit": 0}
    changed_totals: dict[str, int] = {"creation": 0, "edit": 0}
    for row in rows:
        key = _pipeline_cluster_key(row.source)
        if key not in clusters:
            clusters[key] = _empty_pipeline_cluster()
            runtime_totals[key] = 0
            changed_totals[key] = 0
        cluster = clusters[key]
        stats = row.stats if isinstance(row.stats, dict) else {}
        stage_outputs = row.stage_outputs if isinstance(row.stage_outputs, dict) else {}
        input_tokens = int(stats.get("input_tokens", 0) or 0)
        output_tokens = int(stats.get("output_tokens", 0) or 0)
        cluster["runs"] += 1
        cluster["accepted"] += 1 if row.accepted else 0
        cluster["failed"] += 0 if row.accepted else 1
        cluster["input_tokens"] += input_tokens
        cluster["output_tokens"] += output_tokens
        cluster["total_tokens"] += input_tokens + output_tokens
        runtime_totals[key] += _runtime_ms(stats, stage_outputs)
        changed_totals[key] += _changed_file_count(stats, stage_outputs)
        if not cluster["latest_status"]:
            cluster["latest_status"] = row.status
            cluster["latest_provider"] = row.provider
    for key, cluster in clusters.items():
        runs = max(1, int(cluster["runs"]))
        cluster["acceptance_rate"] = round((cluster["accepted"] / runs) * 100, 2) if cluster["runs"] else 0.0
        cluster["avg_runtime_ms"] = round(runtime_totals.get(key, 0) / runs, 2) if cluster["runs"] else 0.0
        cluster["avg_changed_files"] = round(changed_totals.get(key, 0) / runs, 2) if cluster["runs"] else 0.0
    clusters["overall"] = {
        "runs": sum(cluster["runs"] for cluster in clusters.values()),
        "accepted": sum(cluster["accepted"] for cluster in clusters.values()),
        "failed": sum(cluster["failed"] for cluster in clusters.values()),
        "total_tokens": sum(cluster["total_tokens"] for cluster in clusters.values()),
    }
    overall_runs = max(1, clusters["overall"]["runs"])
    clusters["overall"]["acceptance_rate"] = round((clusters["overall"]["accepted"] / overall_runs) * 100, 2) if clusters["overall"]["runs"] else 0.0
    return clusters


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: DbSession) -> list[ProjectOut]:
    items = await ProjectService(db).list_for_user(user)
    return [await _project_out(p, db) for p in items]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, user: CurrentUser, db: DbSession
) -> ProjectOut:
    project = await ProjectService(db).create(payload, user)
    return await _project_out(project, db)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID, user: CurrentUser, db: DbSession) -> ProjectOut:
    project = await ProjectService(db).get_for_user(project_id, user)
    return await _project_out(project, db)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID, payload: ProjectUpdate, user: CurrentUser, db: DbSession
) -> ProjectOut:
    project = await ProjectService(db).update(project_id, payload, user)
    return await _project_out(project, db)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await ProjectService(db).delete(project_id, user)


@router.get("/{project_id}/folder-content", response_model=list[FolderEntry])
async def folder_content(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    path: str = Query(default=""),
) -> list[FolderEntry]:
    project = await ProjectService(db).get_for_user(project_id, user)
    items = await fs.list_folder(project, path)
    return [FolderEntry(**i) for i in items]


@router.get("/{project_id}/stats", response_model=ProjectStats)
async def project_stats(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> ProjectStats:
    project = await ProjectService(db).get_for_user(project_id, user)
    stats = await fs.project_stats(project)
    stats["edit_creation_clusters_metrics"] = await _edit_creation_cluster_metrics(project_id, db)
    return ProjectStats(**stats)


@router.get("/{project_id}/index", response_model=ProjectIndexOut)
async def project_index(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> ProjectIndexOut:
    await ProjectService(db).get_for_user(project_id, user)
    helper = await ProjectHelperRepository(db).get_or_create(project_id)
    schema = helper.database_schema or {}
    return ProjectIndexOut(
        project_id=project_id,
        project_context=helper.project_context,
        database_schema=schema,
        api_routes=helper.api_routes or [],
        file_index=helper.file_index or [],
        function_summaries=helper.function_summaries or [],
        class_summaries=schema.get("class_summaries") or [],
        query_filters=schema.get("query_filters") or [],
        router_wiring=schema.get("router_wiring") or [],
        index_meta=schema.get("index_meta") or {},
        requirement_contracts=helper.requirement_contracts or [],
        recent_changes=helper.recent_changes or [],
    )


@router.post("/{project_id}/index/rebuild", response_model=ProjectIndexOut)
async def rebuild_project_index(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> ProjectIndexOut:
    project = await ProjectService(db).get_for_user(project_id, user)
    await ProjectIndexer(db).refresh(project, reason="manual_rebuild")
    helper = await ProjectHelperRepository(db).get_or_create(project_id)
    schema = helper.database_schema or {}
    return ProjectIndexOut(
        project_id=project_id,
        project_context=helper.project_context,
        database_schema=schema,
        api_routes=helper.api_routes or [],
        file_index=helper.file_index or [],
        function_summaries=helper.function_summaries or [],
        class_summaries=schema.get("class_summaries") or [],
        query_filters=schema.get("query_filters") or [],
        router_wiring=schema.get("router_wiring") or [],
        index_meta=schema.get("index_meta") or {},
        requirement_contracts=helper.requirement_contracts or [],
        recent_changes=helper.recent_changes or [],
    )


@router.get("/{project_id}/requirements")
async def project_requirements_status(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """Return requirement-level coverage and repair targets for one project."""
    await ProjectService(db).get_for_user(project_id, user)
    helper = await ProjectHelperRepository(db).get_or_create(project_id)
    items: list[dict] = []
    seen: set[str] = set()
    for contract in helper.requirement_contracts or []:
        if not isinstance(contract, dict):
            continue
        artifact = contract.get("artifact_validation") if isinstance(contract.get("artifact_validation"), dict) else {}
        missing = [str(item) for item in artifact.get("missing_artifacts") or []]
        regressions = [str(item) for item in artifact.get("regressions") or []]
        warnings = [*missing, *regressions]
        affected = list(contract.get("changed_files") or [])
        for index, requirement in enumerate(contract.get("requirements") or []):
            if isinstance(requirement, dict):
                requirement_id = str(requirement.get("id") or requirement.get("requirement_id") or f"requirement-{index + 1}")
                description = str(requirement.get("description") or requirement.get("name") or requirement_id)
            else:
                requirement_id = f"requirement-{index + 1}"
                description = str(requirement)
            if requirement_id in seen:
                continue
            seen.add(requirement_id)
            items.append({
                "requirement_id": requirement_id,
                "description": description,
                "status": "warning" if warnings else "covered",
                "warnings": warnings[:20],
                "affected_files": affected[:20],
                "dependencies": [],
                "contract_id": contract.get("id") or "",
                "source": contract.get("source") or "",
            })
    return {"project_id": str(project_id), "requirements": items}


@router.post("/{project_id}/requirements/{requirement_id}/repair", status_code=status.HTTP_202_ACCEPTED)
async def repair_project_requirement(
    project_id: UUID,
    requirement_id: str,
    payload: ProjectRunControl,
    user: CurrentUser,
    db: DbSession,
) -> ProjectRunOut:
    """Queue a focused repair run for exactly one requirement."""
    project = await ProjectService(db).get_for_user(project_id, user)
    helper = await ProjectHelperRepository(db).get_or_create(project_id)
    target: dict | None = None
    for contract in helper.requirement_contracts or []:
        if not isinstance(contract, dict):
            continue
        for index, requirement in enumerate(contract.get("requirements") or []):
            item_id = str(requirement.get("id") or requirement.get("requirement_id") or f"requirement-{index + 1}") if isinstance(requirement, dict) else f"requirement-{index + 1}"
            if item_id == requirement_id:
                artifact = contract.get("artifact_validation") if isinstance(contract.get("artifact_validation"), dict) else {}
                target = {
                    "requirement_id": item_id,
                    "description": str(requirement.get("description") or requirement.get("name") or item_id) if isinstance(requirement, dict) else str(requirement),
                    "affected_files": list(contract.get("changed_files") or []),
                    "warnings": [*(artifact.get("missing_artifacts") or []), *(artifact.get("regressions") or [])],
                }
                break
        if target:
            break
    if target is None:
        raise NotFoundError("Requirement not found for this project")
    instruction = payload.instruction.strip()
    prompt = (
        f"Repair only requirement {requirement_id}: {target['description']}. "
        f"Affected files: {', '.join(target['affected_files']) or 'infer from the current project index'}. "
        f"Current validation warnings: {target['warnings'][:12]}. "
        "Do not change unrelated requirements or files. "
        + (f"Additional user instruction: {instruction}" if instruction else "")
    )
    repo = ProjectRunRepository(db)
    run = await repo.create(
        project_id=project_id,
        user_id=user.id,
        operation="edit",
        prompt=prompt,
        provider="auto",
        model_id=payload.model_id,
        idempotency_key=f"requirement:{requirement_id}:{uuid4().hex}",
        request_json={
            "source": "requirement_repair",
            "requirement_id": requirement_id,
            "repair_scope": target,
            "project_id": str(project_id),
        },
    )
    await db.commit()
    await db.refresh(run)
    await publish_run_event(run, "project_run.queued", {"status": run.status, "stage": run.stage, "requirement_id": requirement_id})
    return await run_out(repo, run)

@router.get("/{project_id}/download")
async def download_project(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    bg: BackgroundTasks,
) -> FileResponse:
    project = await ProjectService(db).get_for_user(project_id, user)
    zip_path = await fs.create_zip(project)
    bg.add_task(fs.cleanup_zip, zip_path)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{project.project_code}.zip",
    )


@router.post(
    "/{project_id}/files",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    file_path: str = Form(default=""),
) -> ProjectOut:
    project = await ProjectService(db).get_for_user(project_id, user)

    if not file.filename:
        raise ValidationError("Missing filename")

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise ValidationError(
            f"File too large (max {settings.max_upload_bytes // (1024*1024)} MB)"
        )

    await fs.write_uploaded_file(project, file_path, file.filename, data)
    await ProjectService(db).record_uploaded_file(
        project, file_path, file.filename, len(data), file.content_type or ""
    )
    return await _project_out(project, db)



@router.delete("/{project_id}/files", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    path: str = Query(...),
) -> None:
    project = await ProjectService(db).get_for_user(project_id, user)
    await fs.delete_file(project, path)
    await ProjectIndexer(db).refresh(project, reason="file_delete")
@router.get("/{project_id}/files/download")
async def download_single_file(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    path: str = Query(...),
) -> FileResponse:
    project = await ProjectService(db).get_for_user(project_id, user)
    target = fs.resolve_for_download(project, path)
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        target,
        media_type=mime or "application/octet-stream",
        filename=target.name,
    )


@router.get("/{project_id}/files/content", response_model=FileContent)
async def file_content(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    path: str = Query(...),
) -> FileContent:
    project = await ProjectService(db).get_for_user(project_id, user)
    text = await fs.read_text_file(project, path)
    return FileContent(file_path=path, content=text, size=len(text.encode("utf-8")))
