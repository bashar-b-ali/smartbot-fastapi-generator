import asyncio
import mimetypes
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, get_current_user
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.exceptions import NotFoundError, ValidationError
from app.db.session import SessionLocal
from app.models.llm_config import LLMModelConfig
from app.models.user import User
from app.repositories.chat import ChatMessageRepository
from app.repositories.llm_config import LLMModelConfigRepository
from app.repositories.pipeline_memory import PipelineMemoryRepository
from app.repositories.project_run import ProjectRunRepository
from app.repositories.user import UserRepository
from app.schemas.chat import (
    AnalyzeRequest,
    Attachment,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ChatSessionOut,
    ChatSessionWithMessages,
    CreateSession,
    ExplainCodeRequest,
    FixErrorRequest,
    ProjectAnalysisOut,
    ReviewCodeRequest,
)
from app.schemas.generation import (
    GenerateFromPromptRequest,
    GenerateFromPromptResponse,
    PipelineAuditOut,
    PipelineRunDetailOut,
    PipelineRunOut,
    PipelineTraceEventOut,
    PreviewPlanRequest,
    PreviewPlanResponse,
)
from app.schemas.llm_config import (
    LLMModelOut,
    UserCustomLLMModelCreate,
    UserCustomLLMModelUpdate,
)
from app.services.change_request import build_change_request_state
from app.services.chat import ChatService
from app.services.generator import GeneratorService
from app.services.llm_service import LLMService
from app.services.model_pipeline.llm_io import summarize_trace_value
from app.services.model_pipeline.replay import replay_stage_outputs
from app.services.project_activity import (
    clear_project_activity,
    start_project_activity,
)
from app.services.project_editor import ProjectEditService
from app.services.project_runs import publish_run_event
from app.services.projects import ProjectService
from app.services.rate_limit import enforce_llm_rate_limit

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

CurrentUser = Annotated[User, Depends(get_current_user)]
PIPELINE_RUN_PROMPT_CHARS = 2000
PIPELINE_TRACE_EVENT_LIMIT = 100
CHAT_JOB_TTL = timedelta(hours=6)
PENDING_GENERATION_HEARTBEAT_SECONDS = 10
_chat_jobs: dict[str, dict[str, Any]] = {}
_chat_job_tasks: dict[str, asyncio.Task] = {}
_chat_jobs_lock = asyncio.Lock()


# Helpers
ALLOWED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif",
}
ALLOWED_FILE_MIMES = ALLOWED_IMAGE_MIMES | {
    "application/pdf", "text/plain", "text/markdown", "application/json",
}


class ChatJobStartResponse(BaseModel):
    job_id: str
    status: str = "queued"
    project_id: UUID
    poll_url: str
    message: str = "Chat job queued. Poll until status is succeeded or failed."


class ChatJobStatusResponse(BaseModel):
    job_id: str
    status: str
    project_id: UUID
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    progress: dict[str, Any] = {}
    result: ChatResponse | None = None


def _chat_uploads_dir(project) -> Path:
    """Per-project folder for chat attachments."""
    base = project.folder_path or str(
        Path(settings.projects_root) / "_orphan" / str(project.id)
    )
    return Path(base) / "_chat_uploads"


def _safe_attachment_path(project, filename: str) -> Path:
    """Resolve a stored attachment path, refusing traversal."""
    base = _chat_uploads_dir(project).resolve()
    target = (base / filename).resolve()
    if target != base and base not in target.parents:
        raise ValidationError("Invalid attachment path")
    return target


def _public_pipeline_payload(value: Any) -> Any:
    """Return bounded, redacted debug data for UI inspection."""
    return summarize_trace_value(value)


def _pipeline_run_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "source": row.source,
        "provider": row.provider,
        "status": row.status,
        "prompt": str(row.prompt or "")[:PIPELINE_RUN_PROMPT_CHARS],
        "stats": _public_pipeline_payload(row.stats or {}),
        "stage_summary": _public_pipeline_payload(row.stage_outputs or {}),
        "accepted": row.accepted,
        "created_at": row.created_at,
    }


def _pipeline_event_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "run_id": row.run_id,
        "stage": row.stage,
        "status": row.status,
        "event": _public_pipeline_payload(row.event or {}),
        "created_at": row.created_at,
    }


# Public model picker for any logged-in user.
class ChatModelOption(BaseModel):
    """Minimal model descriptor for the chat picker - no secrets."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID | None = None
    name: str
    provider: str
    model_id: str
    is_default: bool
    source: str = "user"


@router.get("/models", response_model=list[ChatModelOption])
async def list_chat_models(
    user: CurrentUser, db: DbSession
) -> list[ChatModelOption]:
    """Public list of active models available to use in chat. Excludes API keys."""
    repo = LLMModelConfigRepository(db)
    rows = await repo.list_active(user.id)
    user_has_default = any(r.is_default and r.is_active for r in rows)
    options = [
        ChatModelOption(
            id=None,
            name="Server default",
            provider=settings.llm_provider or "ollama",
            model_id=settings.llm_model or "llama3.2",
            is_default=not user_has_default,
            source="server",
        )
    ]
    options.extend(
        ChatModelOption.model_validate(r).model_copy(
            update={"source": "user" if r.user_id else "global"}
        )
        for r in rows
        if r.is_active
    )
    return options


@router.get("/models/custom", response_model=list[LLMModelOut])
async def list_custom_chat_models(user: CurrentUser, db: DbSession) -> list[LLMModelOut]:
    rows = await LLMModelConfigRepository(db).list_user_owned(user.id)
    return [LLMModelOut.model_validate(r) for r in rows]


@router.post("/models/custom", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def create_custom_chat_model(
    payload: UserCustomLLMModelCreate, user: CurrentUser, db: DbSession
) -> LLMModelOut:
    """Let a logged-in user add a private model/API config.

    These configs are scoped to the current user by `user_id`; public/admin
    model configs stay global with `user_id=NULL`.
    """
    obj = LLMModelConfig(
        user_id=user.id,
        name=payload.name,
        provider=payload.provider,
        model_id=payload.model_id,
        api_key=encrypt_secret(payload.api_key),
        api_base_url=payload.api_base_url,
        default_max_tokens=payload.default_max_tokens,
        temperature=payload.temperature,
        is_active=True,
        is_default=payload.is_default,
    )
    repo = LLMModelConfigRepository(db)
    if obj.is_default:
        await repo.clear_user_default_except(user.id)
    created = await repo.add(obj)
    return LLMModelOut.model_validate(created)


@router.patch("/models/custom/{model_id}", response_model=LLMModelOut)
async def update_custom_chat_model(
    model_id: UUID,
    payload: UserCustomLLMModelUpdate,
    user: CurrentUser,
    db: DbSession,
) -> LLMModelOut:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_user_owned(model_id, user.id)
    if not obj:
        raise NotFoundError("Model not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "api_key" and value is not None:
            value = encrypt_secret(value)
        setattr(obj, field, value)
    if obj.is_default:
        await repo.clear_user_default_except(user.id, obj.id)
    await db.flush()
    await db.refresh(obj)
    return LLMModelOut.model_validate(obj)


@router.delete("/models/custom/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_chat_model(
    model_id: UUID, user: CurrentUser, db: DbSession
) -> None:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_user_owned(model_id, user.id)
    if not obj:
        raise NotFoundError("Model not found")
    await repo.delete(obj)


@router.post("/models/custom/{model_id}/set-default", response_model=LLMModelOut)
async def set_custom_chat_model_default(
    model_id: UUID, user: CurrentUser, db: DbSession
) -> LLMModelOut:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_user_owned(model_id, user.id)
    if not obj:
        raise NotFoundError("Model not found")
    obj.is_active = True
    obj.is_default = True
    await repo.clear_user_default_except(user.id, obj.id)
    await db.flush()
    await db.refresh(obj)
    return LLMModelOut.model_validate(obj)


@router.post("/models/use-server-default", status_code=status.HTTP_204_NO_CONTENT)
async def use_server_default_model(user: CurrentUser, db: DbSession) -> None:
    await LLMModelConfigRepository(db).clear_user_default_except(user.id)


@router.post("/projects/{project_id}/analyze", response_model=ProjectAnalysisOut)
async def analyze_project(
    project_id: UUID, payload: AnalyzeRequest, user: CurrentUser, db: DbSession
) -> ProjectAnalysisOut:
    await enforce_llm_rate_limit(user.id)
    analysis = await ChatService(db).analyze(
        project_id, user, payload.description, payload.model_id
    )
    return ProjectAnalysisOut.model_validate(analysis)


@router.get("/projects/{project_id}/analysis", response_model=ProjectAnalysisOut)
async def get_analysis(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> ProjectAnalysisOut:
    analysis = await ChatService(db).get_analysis(project_id, user)
    return ProjectAnalysisOut.model_validate(analysis)


@router.get("/projects/{project_id}/sessions", response_model=list[ChatSessionOut])
async def list_sessions(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> list[ChatSessionOut]:
    rows = await ChatService(db).list_sessions(project_id, user)
    return [ChatSessionOut.model_validate(s) for s in rows]


@router.post(
    "/projects/{project_id}/sessions",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    project_id: UUID, payload: CreateSession, user: CurrentUser, db: DbSession
) -> ChatSessionOut:
    session = await ChatService(db).create_session(project_id, payload.title, user)
    return ChatSessionOut.model_validate(session)


@router.get("/sessions/{session_id}", response_model=ChatSessionWithMessages)
async def get_session(
    session_id: UUID, user: CurrentUser, db: DbSession
) -> ChatSessionWithMessages:
    session = await ChatService(db).get_session_with_messages(session_id, user)
    return ChatSessionWithMessages(
        id=session.id,
        project_id=session.project_id,
        title=session.title,
        is_active=session.is_active,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[ChatMessageOut.model_validate(m) for m in session.messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: UUID, user: CurrentUser, db: DbSession) -> None:
    await ChatService(db).delete_session(session_id, user)




def _build_chat_response(session, user_msg, assistant_msg, generation, context_savings) -> ChatResponse:
    generation = generation or {}
    return ChatResponse(
        session_id=session.id,
        user_message=ChatMessageOut.model_validate(user_msg),
        assistant_message=ChatMessageOut.model_validate(assistant_msg),
        generated=bool(generation.get("files")),
        generated_files=generation.get("files", []),
        view_file_path=generation.get("view_file_path"),
        context_savings=context_savings,
        generation_stats=generation.get("stats", {}),
        edit_plan=generation.get("edit_plan", {}),
        validation=generation.get("validation", {}),
        artifact_validation=generation.get("artifact_validation", {}),
        accepted=bool(generation.get("accepted")),
        pipeline_state=generation.get("pipeline_state", "unknown"),
        failure_category=generation.get("failure_category", ""),
        missing_artifacts=generation.get("missing_artifacts", []),
        regressions=generation.get("regressions", []),
        pipeline_trace=generation.get("pipeline_trace", []),
        stage_timings_ms=generation.get("stage_timings_ms", {}),
        provider=generation.get("provider", ""),
    )


async def _prune_chat_jobs(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    expired = [
        job_id
        for job_id, job in _chat_jobs.items()
        if job.get("status") in {"succeeded", "failed"}
        and now - job.get("updated_at", now) > CHAT_JOB_TTL
    ]
    for job_id in expired:
        _chat_jobs.pop(job_id, None)
        _chat_job_tasks.pop(job_id, None)


async def _set_chat_job(job_id: str, **updates: Any) -> dict[str, Any]:
    async with _chat_jobs_lock:
        job = _chat_jobs[job_id]
        job.update(updates)
        job["updated_at"] = datetime.now(UTC)
        return dict(job)


async def _publish_chat_job(job: dict[str, Any]) -> None:
    from app.realtime.manager import manager

    await manager.publish(
        f"project:{job['project_id']}:chat",
        {
            "type": "chat_job_status",
            "job_id": job["job_id"],
            "status": job["status"],
            "project_id": str(job["project_id"]),
            "progress": job.get("progress") or {},
            "error": job.get("error") or "",
        },
    )


async def _run_chat_job(job_id: str, project_id: UUID, user_id: UUID, payload: dict[str, Any]) -> None:
    await start_project_activity(
        project_id,
        operation="chat_model_job",
        job_id=job_id,
        message="Local model chat job is running.",
    )
    job = await _set_chat_job(
        job_id,
        status="running",
        started_at=datetime.now(UTC),
        progress={"stage": "starting", "message": "Starting local model request."},
    )
    await _publish_chat_job(job)
    async with SessionLocal() as db:
        try:
            await start_project_activity(
                project_id,
                operation="chat_model_job",
                job_id=job_id,
                message="Local model chat job is running.",
                db=db,
            )
            await db.commit()
            user = await UserRepository(db).get_by_id(user_id)
            if not user or not user.is_active:
                raise ValidationError("User not found or inactive")
            request = ChatRequest.model_validate(payload)
            job = await _set_chat_job(
                job_id,
                progress={"stage": "model_running", "message": "Local model is generating. This can take several minutes."},
            )
            await _publish_chat_job(job)
            session, user_msg, assistant_msg, generation, context_savings = await ChatService(db).chat(
                project_id,
                user,
                request.message,
                request.session_id,
                request.model_id,
                provider=request.provider,
                model=request.model,
                attachments=[a.model_dump() for a in request.attachments],
            )
            response = _build_chat_response(session, user_msg, assistant_msg, generation, context_savings)
            await db.commit()
            job = await _set_chat_job(
                job_id,
                status="succeeded",
                finished_at=datetime.now(UTC),
                progress={"stage": "complete", "message": "Chat job completed."},
                result=response.model_dump(mode="json"),
                error="",
            )
            await _publish_chat_job(job)
        except Exception as exc:
            await db.rollback()
            job = await _set_chat_job(
                job_id,
                status="failed",
                finished_at=datetime.now(UTC),
                progress={"stage": "failed", "message": "Chat job failed."},
                error=f"{type(exc).__name__}: {exc}",
            )
            await _publish_chat_job(job)
    async with SessionLocal() as db:
        await clear_project_activity(project_id, job_id=job_id, db=db)
        await db.commit()


@router.post(
    "/projects/{project_id}/chat/start",
    response_model=ChatJobStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_chat_job(
    project_id: UUID, payload: ChatRequest, user: CurrentUser, db: DbSession
) -> ChatJobStartResponse:
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    await _prune_chat_jobs()
    job_id = uuid4().hex
    now = datetime.now(UTC)
    payload_data = payload.model_dump(mode="json")
    async with _chat_jobs_lock:
        _chat_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "project_id": project_id,
            "user_id": user.id,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "error": "",
            "progress": {"stage": "queued", "message": "Queued for local model processing."},
            "result": None,
        }
        _chat_job_tasks[job_id] = asyncio.create_task(
            _run_chat_job(job_id, project_id, user.id, payload_data)
        )
    return ChatJobStartResponse(
        job_id=job_id,
        project_id=project_id,
        poll_url=f"/api/v1/chatbot/projects/{project_id}/chat/jobs/{job_id}",
    )


@router.get("/projects/{project_id}/chat/jobs/{job_id}", response_model=ChatJobStatusResponse)
async def get_chat_job(project_id: UUID, job_id: str, user: CurrentUser, db: DbSession) -> ChatJobStatusResponse:
    await ChatService(db)._ensure_owns_project(project_id, user)
    async with _chat_jobs_lock:
        job = dict(_chat_jobs.get(job_id) or {})
    if not job or job.get("project_id") != project_id or job.get("user_id") != user.id:
        raise NotFoundError("Chat job not found")
    result = ChatResponse.model_validate(job["result"]) if job.get("result") else None
    return ChatJobStatusResponse(
        job_id=job_id,
        status=str(job.get("status") or "unknown"),
        project_id=project_id,
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        error=str(job.get("error") or ""),
        progress=job.get("progress") or {},
        result=result,
    )


def _pending_generation_progress_message(*, editing: bool, stage: str, elapsed_seconds: int = 0) -> str:
    action = "Editing the existing project" if editing else "Creating the project"
    elapsed = f" Elapsed: {elapsed_seconds // 60}m {elapsed_seconds % 60}s." if elapsed_seconds else ""
    return "\n".join(
        [
            f"{action} with the selected local model.",
            "",
            f"Status: {stage}.{elapsed}",
            "",
            "The request is still running in the backend. This saved message updates automatically and will be replaced with the final result when validation finishes.",
        ]
    )


async def _publish_project_refresh_events(
    *,
    project_id: UUID,
    session_id: UUID | None = None,
    accepted: bool | None = None,
    reason: str = "generation_finished",
    files: list[dict[str, Any]] | None = None,
) -> None:
    try:
        from app.realtime.manager import manager

        payload = {
            "type": "project_generation_finished",
            "project_id": str(project_id),
            "session_id": str(session_id) if session_id else "",
            "accepted": accepted,
            "reason": reason,
            "files": files or [],
            "refresh": ["project", "folder-content", "stats", "chat-session", "pipeline-audit"],
        }
        await manager.publish(f"project:{project_id}:chat", payload)
    except Exception:
        return

async def _update_pending_chat_message(
    *,
    project_id: UUID,
    assistant_message_id: UUID,
    content: str,
    model_used: str = "pipeline-running",
) -> None:
    async with SessionLocal() as db:
        message = await ChatMessageRepository(db).get_by_id(assistant_message_id)
        if message is None:
            return
        message = await ChatMessageRepository(db).update(
            message,
            content=content,
            tokens_used=0,
            model_used=model_used,
        )
        await db.commit()
        await ChatService._publish_chat_event(project_id, "chat_message_updated", message)


async def _pending_generation_heartbeat(
    *,
    project_id: UUID,
    assistant_message_id: UUID,
    editing: bool,
    stop_event: asyncio.Event,
) -> None:
    started = datetime.now(UTC)
    try:
        while not stop_event.is_set():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=PENDING_GENERATION_HEARTBEAT_SECONDS)
                break
            elapsed = int((datetime.now(UTC) - started).total_seconds())
            await _update_pending_chat_message(
                project_id=project_id,
                assistant_message_id=assistant_message_id,
                content=_pending_generation_progress_message(
                    editing=editing,
                    stage="Local model is still generating",
                    elapsed_seconds=elapsed,
                ),
            )
    except Exception:
        return

async def _finish_pending_chat_generation(
    project_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
    assistant_message_id: UUID,
) -> None:
    activity_job_id = str(assistant_message_id)
    activity_status = "failed"
    await start_project_activity(
        project_id,
        operation="project_generation",
        job_id=activity_job_id,
        message="Local model project generation/edit is running.",
    )
    async with SessionLocal() as db:
        assistant_msg = None
        try:
            await start_project_activity(
                project_id,
                operation="project_generation",
                job_id=activity_job_id,
                message="Local model project generation/edit is running.",
                db=db,
            )
            await db.commit()
            user = await UserRepository(db).get_by_id(user_id)
            if not user or not user.is_active:
                raise ValidationError("User not found or inactive")
            request = ChatRequest.model_validate(payload)
            assistant_msg = await ChatMessageRepository(db).get_by_id(assistant_message_id)
            if assistant_msg is None:
                raise ValidationError("Pending assistant message not found")
            history_rows = await ChatMessageRepository(db).for_session(assistant_msg.session_id, limit=20)
            change_state = build_change_request_state(history_rows, request.message)
            project = await ProjectService(db).get_for_user(project_id, user)
            entrypoint = Path(project.folder_path or "") / "main.py"
            has_generated_project = bool(project.folder_path and entrypoint.exists())
            assistant_msg = await ChatMessageRepository(db).update(
                assistant_msg,
                content=_pending_generation_progress_message(
                    editing=has_generated_project,
                    stage="Local model request started",
                ),
                tokens_used=0,
                model_used="pipeline-running",
            )
            await db.commit()
            await ChatService._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
            heartbeat_stop = asyncio.Event()
            heartbeat = asyncio.create_task(
                _pending_generation_heartbeat(
                    project_id=project_id,
                    assistant_message_id=assistant_message_id,
                    editing=has_generated_project,
                    stop_event=heartbeat_stop,
                )
            )
            try:
                if has_generated_project:
                    generation = await ProjectEditService(db).edit_project(
                        project_id,
                        user,
                        prompt=request.message,
                        change_state=change_state,
                        model_id=request.model_id,
                        provider_type=request.provider,
                        model=request.model,
                    )
                else:
                    generation = await GeneratorService(db).generate_from_prompt(
                        project_id,
                        user,
                        prompt=request.message,
                        provider=request.provider or "auto",
                        model=request.model,
                        model_id=request.model_id,
                        clean=False,
                    )
            finally:
                heartbeat_stop.set()
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            stats = generation.get("stats") or {}
            assistant_msg = await ChatMessageRepository(db).update(
                assistant_msg,
                content=ChatService._generation_chat_message(generation),
                tokens_used=int(stats.get("input_tokens", 0) or 0) + int(stats.get("output_tokens", 0) or 0),
                model_used=f"generator:{generation.get('provider') or 'auto'}"[:50],
            )
            await db.commit()
            await ChatService._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
            await _publish_project_refresh_events(
                project_id=project_id,
                session_id=assistant_msg.session_id,
                accepted=bool(generation.get("accepted")),
                reason="generation_finished",
                files=generation.get("files") or generation.get("all_files") or [],
            )
            activity_status = "finished"
        except Exception as exc:
            await db.rollback()
            if assistant_msg is None:
                return
            assistant_msg = await ChatMessageRepository(db).update(
                assistant_msg,
                content=ChatService._generation_failure_message(f"{type(exc).__name__}: {exc}"),
                tokens_used=0,
                model_used="pipeline-provider-error",
            )
            await db.commit()
            await ChatService._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
            await _publish_project_refresh_events(
                project_id=project_id,
                session_id=assistant_msg.session_id,
                accepted=False,
                reason="generation_failed",
                files=[],
            )
        finally:
            await clear_project_activity(project_id, job_id=activity_job_id, db=db, status=activity_status)
            await db.commit()

@router.post("/projects/{project_id}/chat", response_model=ChatResponse)
async def chat(
    project_id: UUID, payload: ChatRequest, user: CurrentUser, db: DbSession
) -> ChatResponse:
    await enforce_llm_rate_limit(user.id)
    service = ChatService(db)
    attachments = [a.model_dump() for a in payload.attachments]
    if service._is_file_mutation_request(payload.message):
        session, user_msg, assistant_msg, context_savings = await service.start_generation_message(
            project_id,
            user,
            payload.message,
            payload.session_id,
            attachments=attachments,
        )
        if not hasattr(db, "execute"):
            asyncio.create_task(
                _finish_pending_chat_generation(
                    project_id,
                    user.id,
                    payload.model_dump(mode="json"),
                    assistant_msg.id,
                )
            )
            return _build_chat_response(
                session, user_msg, assistant_msg, None, context_savings
            )
        project = await ProjectService(db).get_for_user(project_id, user)
        root = Path(project.folder_path or "")
        operation = "edit" if root.exists() and any(root.rglob("*.py")) else "create"
        request_json = payload.model_dump(mode="json")
        request_json.update(
            {
                "assistant_message_id": str(assistant_msg.id),
                "session_id": str(session.id),
                "source": "chat",
                "provider": payload.provider or "auto",
            }
        )
        run_repo = ProjectRunRepository(db)
        run = await run_repo.create(
            project_id=project_id,
            user_id=user.id,
            operation=operation,
            prompt=payload.message,
            provider=payload.provider or "auto",
            model_id=payload.model_id,
            idempotency_key=f"chat:{assistant_msg.id}",
            request_json=request_json,
        )
        assistant_msg = await ChatMessageRepository(db).update(
            assistant_msg,
            content=(
                f"{operation.title()} request queued.\n\n"
                f"Run: {run.id}\n"
                "Progress is saved and will continue across refreshes or backend restarts."
            ),
            tokens_used=0,
            model_used=f"project-run:{run.id}"[:50],
        )
        await db.commit()
        await publish_run_event(
            run,
            "project_run.queued",
            {"status": run.status, "stage": run.stage, "source": "chat"},
        )
        await ChatService._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
        response = _build_chat_response(session, user_msg, assistant_msg, None, context_savings)
        return response.model_copy(update={"run_id": run.id, "run_status": run.status})
    session, user_msg, assistant_msg, generation, context_savings = await service.chat(
        project_id,
        user,
        payload.message,
        payload.session_id,
        payload.model_id,
        provider=payload.provider,
        model=payload.model,
        attachments=attachments,
    )
    return _build_chat_response(session, user_msg, assistant_msg, generation, context_savings)


@router.post("/projects/{project_id}/upload-attachment", response_model=Attachment)
async def upload_chat_attachment(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> Attachment:
    """Upload a chat attachment (image/file). Returns a descriptor the client
    then attaches to its next chat message."""
    project = await ProjectService(db).get_for_user(project_id, user)

    if not file.filename:
        raise ValidationError("Missing filename")

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise ValidationError(
            f"File too large (max {settings.max_upload_bytes // (1024 * 1024)} MB)"
        )

    mime = (file.content_type or "").lower() or (
        mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    )
    if mime not in ALLOWED_FILE_MIMES:
        raise ValidationError(f"Unsupported file type: {mime}")

    # Store with an unguessable random prefix to prevent enumeration.
    suffix = Path(file.filename).suffix.lower()[:10]
    stored_name = f"{secrets.token_hex(16)}{suffix}"

    folder = _chat_uploads_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / stored_name
    target.write_bytes(data)

    kind = "image" if mime in ALLOWED_IMAGE_MIMES else "file"
    url = f"/api/v1/chatbot/projects/{project_id}/attachments/{stored_name}"

    return Attachment(
        kind=kind,
        name=file.filename,
        url=url,
        mime=mime,
        size=len(data),
    )


@router.get("/projects/{project_id}/attachments/{filename}")
async def fetch_chat_attachment(
    project_id: UUID,
    filename: str,
    user: CurrentUser,
    db: DbSession,
) -> FileResponse:
    """Serve a previously-uploaded chat attachment, gated by project ownership."""
    project = await ProjectService(db).get_for_user(project_id, user)
    target = _safe_attachment_path(project, filename)
    if not target.exists() or not target.is_file():
        raise NotFoundError("Attachment not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream", filename=target.name)


@router.post("/projects/{project_id}/generate-code", deprecated=True, include_in_schema=False)
async def generate_code(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> dict:
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    return {
        "deprecated": True,
        "pipeline_state": "legacy_generation_disabled",
        "message": "This endpoint is disabled. Use /projects/{project_id}/generate-from-prompt.",
        "replacement": f"/api/v1/chatbot/projects/{project_id}/generate-from-prompt",
    }


@router.post("/projects/{project_id}/generate-app", deprecated=True, include_in_schema=False)
async def generate_app(
    project_id: UUID, user: CurrentUser, db: DbSession
) -> dict:
    """Compatibility endpoint for structured FastAPI app generation."""
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    return {
        "deprecated": True,
        "pipeline_state": "legacy_generation_disabled",
        "message": "This endpoint is disabled. Use /projects/{project_id}/generate-from-prompt.",
        "replacement": f"/api/v1/chatbot/projects/{project_id}/generate-from-prompt",
    }


@router.get("/projects/{project_id}/pipeline-runs", response_model=list[PipelineRunOut])
async def list_pipeline_runs(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PipelineRunOut]:
    await ChatService(db)._ensure_owns_project(project_id, user)
    rows = await PipelineMemoryRepository(db).list_runs(project_id, limit=limit, offset=offset)
    return [PipelineRunOut.model_validate(_pipeline_run_payload(row)) for row in rows]


@router.get("/projects/{project_id}/pipeline-runs/{run_id}", response_model=PipelineRunDetailOut)
async def get_pipeline_run(
    project_id: UUID,
    run_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> PipelineRunDetailOut:
    await ChatService(db)._ensure_owns_project(project_id, user)
    repo = PipelineMemoryRepository(db)
    run = await repo.get_run(project_id, run_id)
    if not run:
        raise NotFoundError("Pipeline run not found")
    events = (await repo.trace_events(project_id, run_id))[:PIPELINE_TRACE_EVENT_LIMIT]
    stage_outputs = run.stage_outputs if isinstance(run.stage_outputs, dict) else {}
    api_contract = stage_outputs.get("api_contract") if isinstance(stage_outputs.get("api_contract"), dict) else {}
    replay = replay_stage_outputs(
        stage_outputs,
        api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {},
    )
    return PipelineRunDetailOut.model_validate(
        {
            **PipelineRunOut.model_validate(_pipeline_run_payload(run)).model_dump(),
            "replay": _public_pipeline_payload(replay),
            "trace_events": [
                PipelineTraceEventOut.model_validate(_pipeline_event_payload(event))
                for event in events
            ],
        }
    )


@router.get("/projects/{project_id}/pipeline-audit", response_model=PipelineAuditOut)
async def get_pipeline_audit(
    project_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> PipelineAuditOut:
    await ChatService(db)._ensure_owns_project(project_id, user)
    repo = PipelineMemoryRepository(db)
    runs = await repo.list_runs(project_id, limit=1, offset=0)
    if not runs:
        return PipelineAuditOut(project_id=project_id)
    run = runs[0]
    run_out = PipelineRunOut.model_validate(_pipeline_run_payload(run))
    stage_outputs = run.stage_outputs if isinstance(run.stage_outputs, dict) else {}
    validation = stage_outputs.get("validation") if isinstance(stage_outputs.get("validation"), dict) else {}
    trace = stage_outputs.get("trace") if isinstance(stage_outputs.get("trace"), list) else []
    trace_stages = [
        {
            "stage": item.get("stage"),
            "status": item.get("status"),
            "duration_ms": item.get("duration_ms"),
            "input_tokens": item.get("input_tokens"),
            "output_tokens": item.get("output_tokens"),
        }
        for item in trace[-PIPELINE_TRACE_EVENT_LIMIT:]
        if isinstance(item, dict)
    ]
    artifact_validation = (
        validation.get("artifact_validation")
        if isinstance(validation.get("artifact_validation"), dict)
        else {}
    )
    return PipelineAuditOut(
        project_id=project_id,
        latest_run=run_out,
        trace_stages=trace_stages,
        missing_artifacts=artifact_validation.get("missing_artifacts") or [],
        regressions=artifact_validation.get("regressions") or [],
        pipeline_state=str(validation.get("pipeline_state") or ("accepted" if run.accepted else "unknown")),
        accepted=bool(run.accepted),
    )


@router.post(
    "/projects/{project_id}/generate-from-prompt",
    response_model=GenerateFromPromptResponse,
)
async def generate_from_prompt(
    project_id: UUID,
    payload: GenerateFromPromptRequest,
    user: CurrentUser,
    db: DbSession,
) -> GenerateFromPromptResponse:
    """Model-owned generation route.

    The model returns requirements, artifact checks, and source files. The
    service validates candidates in a temp project, writes static-safe output,
    and reports whether the artifact gate fully accepted the result.
    """
    await enforce_llm_rate_limit(user.id)
    result = await GeneratorService(db).generate_from_prompt(
        project_id,
        user,
        prompt=payload.prompt,
        provider=payload.provider,
        model=payload.model,
        model_id=payload.model_id,
        clean=payload.clean,
    )
    return GenerateFromPromptResponse.model_validate(result)


@router.post(
    "/projects/{project_id}/preview-plan",
    response_model=PreviewPlanResponse,
)
async def preview_plan(
    project_id: UUID,
    payload: PreviewPlanRequest,
    user: CurrentUser,
    db: DbSession,
) -> PreviewPlanResponse:
    """Dry-run companion to /generate-from-prompt: produces the plan + the file
    list that *would* be written, but persists nothing. Useful for UI confirm-step."""
    await enforce_llm_rate_limit(user.id)
    result = await GeneratorService(db).preview_plan(
        project_id,
        user,
        prompt=payload.prompt,
        provider=payload.provider,
        model=payload.model,
        model_id=payload.model_id,
    )
    return PreviewPlanResponse.model_validate(result)


@router.post("/projects/{project_id}/review-code")
async def review_code(
    project_id: UUID, payload: ReviewCodeRequest, user: CurrentUser, db: DbSession
) -> dict:
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    return await LLMService(db, user.id).review_code(
        payload.code, context=payload.context, model_id=payload.model_id
    )


@router.post("/projects/{project_id}/fix-error")
async def fix_error(
    project_id: UUID, payload: FixErrorRequest, user: CurrentUser, db: DbSession
) -> dict:
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    return await LLMService(db, user.id).fix_error(
        payload.error,
        payload.code,
        traceback=payload.traceback,
        context=payload.context,
        model_id=payload.model_id,
    )


@router.post("/projects/{project_id}/explain-code")
async def explain_code(
    project_id: UUID, payload: ExplainCodeRequest, user: CurrentUser, db: DbSession
) -> dict:
    await enforce_llm_rate_limit(user.id)
    await ChatService(db)._ensure_owns_project(project_id, user)
    return await LLMService(db, user.id).explain_code(payload.code, model_id=payload.model_id)


@router.get("/model-info")
async def model_info(user: CurrentUser, db: DbSession) -> dict:  # noqa: ARG001
    await enforce_llm_rate_limit(user.id)
    return await LLMService(db, user.id).model_info()


@router.get("/health")
async def llm_health(db: DbSession) -> dict:
    ok = await LLMService(db).health_check()
    return {"status": "ok" if ok else "unavailable"}
