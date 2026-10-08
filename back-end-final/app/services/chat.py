import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.db.mixins import utcnow
from app.models.chat import ChatMessage, ChatSession, ProjectAnalysis
from app.models.user import User
from app.repositories.chat import (
    ChatMessageRepository,
    ChatSessionRepository,
    ProjectAnalysisRepository,
    ProjectHelperRepository,
)
from app.repositories.project import ProjectRepository
from app.services.change_request import build_change_request_state
from app.services.generator import GeneratorService
from app.services.llm_service import LLMService
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.project_context import (
    build_project_context,
    compact_history,
    context_token_savings,
)
from app.services.project_editor import ProjectEditService


class ChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.sessions = ChatSessionRepository(db)
        self.messages = ChatMessageRepository(db)
        self.analyses = ProjectAnalysisRepository(db)
        self.helpers = ProjectHelperRepository(db)
        self.projects = ProjectRepository(db)
        self.llm = LLMService(db)

    @staticmethod
    def _is_project_scoped(message: str, attachments: list[dict] | None = None) -> bool:
        text = (message or "").lower()
        if attachments:
            return True
        if ChatService._looks_like_implicit_mutation(text):
            return True
        allow_terms = {
            "add", "api", "app", "auth", "backend", "bug", "build", "change",
            "code", "create", "crud", "database", "db", "endpoint", "endpoints",
            "error", "fastapi", "file", "fix", "generate", "implement", "login",
            "make", "model", "modify", "project", "pydantic", "route", "routes",
            "router", "schema", "scaffold", "service", "sql", "sqlalchemy",
            "sqlmodel", "test", "update", "upload", "websocket", "write",
        }
        return any(term in text for term in allow_terms)

    @staticmethod
    def _looks_like_implicit_mutation(text: str) -> bool:
        if not text:
            return False
        subject = r"(?:the\s+)?[a-z][a-z0-9_ -]{1,40}"
        patterns = [
            rf"\b{subject}\s+(?:will|should|must|needs?\s+to|can)\s+(?:also\s+)?(?:have|include|store|track|contain)\b",
            rf"\b{subject}\s+(?:needs?|requires?)\s+(?:a|an|the\s+)?(?:new\s+)?[a-z][a-z0-9_ -]*\b",
            r"\b(?:add|include|store|track)\s+(?:a|an|the\s+)?[a-z][a-z0-9_ -]{1,80}\s+(?:to|in|on|for)\s+(?:the\s+)?[a-z]",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _scope_refusal() -> str:
        return (
            "Working summary:\n"
            "- I can only help create, update, review, or explain files for this FastAPI project.\n\n"
            "Files:\n"
            "- No files changed.\n\n"
            "Next step:\n"
            "Tell me which FastAPI file or backend feature you want to build or update."
        )

    @staticmethod
    def _is_file_mutation_request(message: str) -> bool:
        text = (message or "").strip().lower()
        if not text:
            return False

        if ChatService._looks_like_implicit_mutation(text):
            return True

        has_action = re.search(
            r"\b(create|generate|build|scaffold|make|add|implement|update|modify|change|fix|write|improve|harden|secure|validate|refactor|restructure|optimize|clean|include|store|track)\b",
            text,
        )
        if not has_action:
            return False
        if re.search(r"\b(?:to|in|on|for)\s+(?:the\s+)?[a-z][a-z0-9_ -]{1,40}\b", text):
            return True

        target_terms = {
            "app", "api", "auth", "backend", "crud", "database", "db",
            "endpoint", "endpoints", "fastapi", "file", "files", "model",
            "project", "pydantic", "route", "router", "schema", "service",
            "sql", "sqlalchemy", "sqlmodel", "test", "tests", "upload",
            "websocket", "production", "readiness", "validation", "structure",
            "security", "permission", "ownership", "pagination", "response",
            "output",
        }
        return any(term in text for term in target_terms)

    @staticmethod
    def _message_event_payload(message: ChatMessage) -> dict:
        return {
            "id": str(message.id),
            "session_id": str(message.session_id),
            "message_type": message.message_type,
            "content": message.content,
            "tokens_used": message.tokens_used,
            "model_used": message.model_used,
            "attachments": message.attachments or [],
            "created_at": message.created_at.isoformat() if message.created_at else "",
        }

    @staticmethod
    async def _publish_chat_event(project_id: UUID, event_type: str, message: ChatMessage) -> None:
        try:
            from app.realtime.manager import manager

            await manager.publish(
                f"project:{project_id}:chat",
                {
                    "type": event_type,
                    "project_id": str(project_id),
                    "session_id": str(message.session_id),
                    "message": ChatService._message_event_payload(message),
                },
            )
        except Exception:
            return
    @staticmethod
    def _generation_pending_message(*, editing: bool) -> str:
        action = "Editing the existing project" if editing else "Creating the project"
        return "\n".join([
            f"{action} with the selected local model.",
            "",
            "Ollama can take several minutes on local hardware. This message is saved and will update automatically when validation finishes.",
        ])


    @staticmethod
    def _generation_chat_message(result: dict) -> str:
        summaries = result.get("working_summary") or [
            "Generated project files from the request.",
            "Wrote the files directly into the project folder.",
        ]
        files = result.get("files") or []
        validation = result.get("validation") or {}
        stats = result.get("stats") or {}

        lines = ["Working summary:"]
        lines.extend(f"- {item}" for item in summaries[:4])

        lines.extend(["", "Files:"])
        if files:
            for item in files[:12]:
                path = item.get("path", "")
                size = item.get("bytes_written", 0)
                lines.append(f"- {path} ({size} bytes)")
            if len(files) > 12:
                lines.append(f"- ...and {len(files) - 12} more files")
        else:
            lines.append("- No files changed.")

        validation_obj = validation or result.get("validation") or {}
        artifact_validation = (
            validation_obj.get("artifact_validation")
            or result.get("artifact_validation")
            or {}
        )
        completed_quests = (
            result.get("completed_quests")
            or validation_obj.get("completed_quests")
            or artifact_validation.get("completed_quests")
            or []
        )
        failed_quests = (
            result.get("failed_quests")
            or validation_obj.get("failed_quests")
            or artifact_validation.get("failed_quests")
            or []
        )
        missing_artifacts = (
            result.get("missing_artifacts")
            or validation_obj.get("missing_artifacts")
            or artifact_validation.get("missing_artifacts")
            or []
        )
        changed_paths = (
            result.get("changed_files")
            or result.get("candidate_files")
            or result.get("rendered_paths")
            or []
        )

        if completed_quests or failed_quests or missing_artifacts or changed_paths:
            lines.extend(["", "Quest summary:"])
            if completed_quests:
                lines.append(f"- Completed quests: {len(completed_quests)}")
                for quest in completed_quests[:5]:
                    lines.append(f"- Done: {quest}")
            if failed_quests:
                lines.append(f"- Failed quests: {len(failed_quests)}")
                for quest in failed_quests[:5]:
                    lines.append(f"- Failed: {quest}")
            if missing_artifacts:
                lines.append(f"- Missing artifacts: {len(missing_artifacts)}")
                for artifact in missing_artifacts[:5]:
                    lines.append(f"- Missing: {artifact}")
            if changed_paths:
                lines.append(f"- Files that changed: {', '.join(str(path) for path in changed_paths[:8])}")

        if stats.get("full_context_estimated_tokens"):
            lines.extend(["", "Context savings:"])
            lines.append(
                "- "
                f"{stats.get('full_context_estimated_tokens', 0)} -> "
                f"{stats.get('selected_context_estimated_tokens', 0)} estimated tokens "
                f"({stats.get('context_tokens_saved_pct', 0.0)}% saved)"
            )

        if validation:
            passed = validation.get("passed")
            accepted = validation.get("accepted")
            artifact_validation = validation.get("artifact_validation") or result.get("artifact_validation") or {}
            pipeline_trace = (
                result.get("pipeline_trace")
                or (validation.get("model_generation") or {}).get("trace")
                or (validation.get("model_edit") or {}).get("trace")
                or []
            )
            static_passed = not (validation.get("missing_required") or []) and all(
                check.get("passed") is not False
                for check in (validation.get("checks") or [])
                if isinstance(check, dict)
            )
            lines.extend(["", "Validation:"])
            lines.append(f"- Static validation: {'passed' if static_passed else 'failed'}")
            if artifact_validation:
                lines.append(
                    "- Requirement validation: "
                    + ("passed" if artifact_validation.get("passed") else "failed")
                )
            lines.append(f"- Accepted: {'yes' if accepted is True or passed is True else 'no'}")
            if passed is False or accepted is False:
                errors = validation.get("errors") or []
                checks = validation.get("checks") or []
                for err in errors[:3]:
                    lines.append(f"- {err}")
                for err in (validation.get("missing_required") or [])[:3]:
                    lines.append(f"- Missing required file: {err}")
                for err in (validation.get("missing_artifacts") or artifact_validation.get("missing_artifacts") or [])[:3]:
                    lines.append(f"- Missing artifact: {err}")
                for err in (validation.get("regressions") or artifact_validation.get("regressions") or [])[:3]:
                    lines.append(f"- Regression: {err}")
                for check in checks[:3]:
                    if isinstance(check, dict) and check.get("passed") is False:
                        lines.append(f"- {check.get('path')}: {check.get('error')}")
                if pipeline_trace:
                    lines.extend(["", "Pipeline trace:"])
                    for event in pipeline_trace[-4:]:
                        if not isinstance(event, dict):
                            continue
                        stage = event.get("stage") or "stage"
                        status = event.get("status") or "-"
                        duration = event.get("duration_ms")
                        token_text = ""
                        if event.get("input_tokens") or event.get("output_tokens"):
                            token_text = f", tokens {event.get('input_tokens', 0)}/{event.get('output_tokens', 0)}"
                        duration_text = f", {duration} ms" if duration is not None else ""
                        lines.append(f"- {stage}: {status}{duration_text}{token_text}")
                        err = event.get("error")
                        if err:
                            lines.append(f"  error: {str(err)[:220]}")
            coverage = validation.get("requirement_coverage") or {}
            if coverage:
                covered = coverage.get("covered") or []
                missing = coverage.get("missing") or []
                partial = coverage.get("partial") or []
                lines.extend(["", "AI semantic notes:"])
                lines.append(f"- Covered: {len(covered)}")
                lines.append(f"- Missing: {len(missing)}")
                lines.append(f"- Partial: {len(partial)}")
                for item in missing[:3]:
                    lines.append(f"- Missing requirement: {item}")
                for item in partial[:3]:
                    if isinstance(item, dict):
                        label = item.get("requirement_id") or item.get("id") or "partial"
                        needed = item.get("needed_change") or item.get("reason") or ""
                        lines.append(f"- Partial requirement {label}: {needed}")

        edit_plan = result.get("edit_plan") or {}
        requirements = edit_plan.get("requirements") or []
        if requirements:
            lines.extend(["", "Requirements understood:"])
            for req in requirements[:5]:
                if isinstance(req, dict):
                    label = req.get("id") or "-"
                    desc = req.get("description") or ""
                    lines.append(f"- {label}: {desc}")
        operations = edit_plan.get("operations") or []
        if operations:
            lines.extend(["", "Plan:"])
            for op in operations[:4]:
                if isinstance(op, dict):
                    label = op.get("type") or op.get("intent") or "operation"
                    path = op.get("path")
                    lines.append(f"- {label}{f' `{path}`' if path else ''}")

        lines.extend([
            "",
            "Next step:",
            result.get("next_prompt") or "Do you want to view a generated file?",
        ])
        return "\n".join(lines)

    @staticmethod
    def _generation_failure_message(message: str) -> str:
        return "\n".join([
            "Working summary:",
            "- The backend understood this as a project generation/edit request, but the pipeline failed before writing files.",
            f"- Failure: {message}",
            "",
            "Files:",
            "- No files changed.",
            "",
            "Validation:",
            "- The request was saved in chat, so the next message can continue from this context.",
        ])

    async def _ensure_owns_project(self, project_id: UUID, user: User):
        project = await self.projects.get_for_user(project_id, user.id)
        if not project:
            raise NotFoundError("Project not found")
        return project

    async def list_sessions(self, project_id: UUID, user: User) -> list[ChatSession]:
        await self._ensure_owns_project(project_id, user)
        return await self.sessions.list_for_project(project_id)

    async def create_session(self, project_id: UUID, title: str, user: User) -> ChatSession:
        await self._ensure_owns_project(project_id, user)
        return await self.sessions.add(ChatSession(project_id=project_id, title=title))

    async def get_session_with_messages(
        self, session_id: UUID, user: User
    ) -> ChatSession:
        session = await self.sessions.with_messages(session_id)
        if not session:
            raise NotFoundError("Chat session not found")
        await self._ensure_owns_project(session.project_id, user)
        return session

    async def delete_session(self, session_id: UUID, user: User) -> None:
        session = await self.sessions.get_by_id(session_id)
        if not session:
            raise NotFoundError("Chat session not found")
        await self._ensure_owns_project(session.project_id, user)
        await self.sessions.delete(session)

    async def _pending_generation_pair(
        self,
        project_id: UUID,
        message: str,
    ) -> tuple[ChatSession, ChatMessage, ChatMessage] | None:
        rows = await self.messages.recent_for_project(project_id, limit=30)
        normalized = (message or "").strip()
        for idx in range(len(rows) - 1, -1, -1):
            assistant = rows[idx]
            if assistant.message_type != "assistant" or assistant.model_used != "pipeline-running":
                continue
            for prev in range(idx - 1, -1, -1):
                user_msg = rows[prev]
                if user_msg.message_type != "user":
                    continue
                if (user_msg.content or "").strip() == normalized:
                    session = await self.sessions.get_by_id(user_msg.session_id)
                    if session is not None:
                        return session, user_msg, assistant
                break
        return None
    async def start_generation_message(
        self,
        project_id: UUID,
        user: User,
        message: str,
        session_id: UUID | None,
        *,
        attachments: list[dict] | None = None,
    ) -> tuple[ChatSession, ChatMessage, ChatMessage, dict]:
        project = await self._ensure_owns_project(project_id, user)
        analysis = await self.analyses.for_project(project_id)
        helper = await self.helpers.for_project(project_id)
        context_savings = context_token_savings(
            analysis=analysis,
            helper=helper,
            query=message,
        )

        existing_pair = await self._pending_generation_pair(project_id, message)
        if existing_pair is not None:
            existing_session, user_msg, assistant_msg = existing_pair
            return existing_session, user_msg, assistant_msg, context_savings

        if session_id:
            session = await self.sessions.get_by_id(session_id)
            if not session or session.project_id != project_id:
                raise NotFoundError("Chat session not found")
            await self._ensure_owns_project(session.project_id, user)
        else:
            session = await self.sessions.add(ChatSession(project_id=project_id, title="New Chat"))

        user_created_at = utcnow()
        assistant_created_at = user_created_at + timedelta(seconds=1)
        user_msg = await self.messages.add(
            ChatMessage(
                session_id=session.id,
                message_type="user",
                content=message,
                attachments=list(attachments or []),
                created_at=user_created_at,
            )
        )
        indexed_files = (getattr(helper, "file_index", None) or []) if helper else []
        entrypoint = FASTAPI_PROFILE.entrypoint_file
        has_generated_project = any(
            item.get("path") == entrypoint for item in indexed_files if isinstance(item, dict)
        ) or bool(getattr(project, "folder_path", "") and (Path(project.folder_path) / entrypoint).exists())
        assistant_msg = await self.messages.add(
            ChatMessage(
                session_id=session.id,
                message_type="assistant",
                content=self._generation_pending_message(editing=has_generated_project),
                tokens_used=0,
                model_used="pipeline-running",
                attachments=[],
                created_at=assistant_created_at,
            )
        )
        await self.db.commit()
        await self._publish_chat_event(project_id, "chat_message_created", user_msg)
        await self._publish_chat_event(project_id, "chat_message_created", assistant_msg)
        return session, user_msg, assistant_msg, context_savings

    async def chat(
        self,
        project_id: UUID,
        user: User,
        message: str,
        session_id: UUID | None,
        model_id: UUID | None,
        provider: str | None = None,
        model: str | None = None,
        attachments: list[dict] | None = None,
    ) -> tuple[ChatSession, ChatMessage, ChatMessage, dict | None, dict]:
        project = await self._ensure_owns_project(project_id, user)

        if session_id:
            session = await self.sessions.get_by_id(session_id)
            if not session or session.project_id != project_id:
                raise NotFoundError("Chat session not found")
            await self._ensure_owns_project(session.project_id, user)
        else:
            session = await self.sessions.add(ChatSession(project_id=project_id, title="New Chat"))

        analysis = await self.analyses.for_project(project_id)
        helper = await self.helpers.for_project(project_id)
        ctx = build_project_context(analysis=analysis, helper=helper, query=message)
        context_savings = context_token_savings(
            analysis=analysis,
            helper=helper,
            query=message,
        )

        history_rows = await self.messages.for_session(session.id, limit=20)
        history = compact_history(history_rows)
        change_state = build_change_request_state(history_rows, message)
        is_continuation = change_state.confirmation and change_state.has_context

        user_created_at = utcnow()
        assistant_created_at = user_created_at + timedelta(seconds=1)
        user_msg = await self.messages.add(
            ChatMessage(
                session_id=session.id,
                message_type="user",
                content=message,
                attachments=list(attachments or []),
                created_at=user_created_at,
            )
        )

        if not self._is_project_scoped(message, attachments) and not is_continuation:
            assistant_msg = await self.messages.add(
                ChatMessage(
                    session_id=session.id,
                    message_type="assistant",
                    content=self._scope_refusal(),
                    tokens_used=0,
                    model_used="scope-guard",
                    attachments=[],
                    created_at=assistant_created_at,
                )
            )
            return session, user_msg, assistant_msg, None, context_savings

        if self._is_file_mutation_request(message) or is_continuation:
            indexed_files = (getattr(helper, "file_index", None) or []) if helper else []
            entrypoint = FASTAPI_PROFILE.entrypoint_file
            has_generated_project = any(
                item.get("path") == entrypoint for item in indexed_files if isinstance(item, dict)
            ) or bool(getattr(project, "folder_path", "") and (Path(project.folder_path) / entrypoint).exists())
            assistant_msg = await self.messages.add(
                ChatMessage(
                    session_id=session.id,
                    message_type="assistant",
                    content=self._generation_pending_message(editing=has_generated_project),
                    tokens_used=0,
                    model_used="pipeline-running",
                    attachments=[],
                    created_at=assistant_created_at,
                )
            )
            await self.db.commit()
            await self._publish_chat_event(project_id, "chat_message_created", user_msg)
            await self._publish_chat_event(project_id, "chat_message_created", assistant_msg)
            if has_generated_project:
                try:
                    generation = await ProjectEditService(self.db).edit_project(
                        project_id,
                        user,
                        prompt=message,
                        change_state=change_state,
                        model_id=model_id,
                        provider_type=provider,
                        model=model,
                    )
                except ValidationError as exc:
                    assistant_msg = await self.messages.update(
                        assistant_msg,
                        content=self._generation_failure_message(exc.message),
                        tokens_used=0,
                        model_used="pipeline-validation",
                    )
                    await self.db.commit()
                    await self._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
                    return session, user_msg, assistant_msg, None, context_savings
                except Exception as exc:
                    assistant_msg = await self.messages.update(
                        assistant_msg,
                        content=self._generation_failure_message(f"{type(exc).__name__}: {exc}"),
                        tokens_used=0,
                        model_used="pipeline-provider-error",
                    )
                    await self.db.commit()
                    await self._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
                    return session, user_msg, assistant_msg, None, context_savings
            else:
                try:
                    generation = await GeneratorService(self.db).generate_from_prompt(
                        project_id,
                        user,
                        prompt=message,
                        provider=provider or "auto",
                        model=model,
                        model_id=model_id,
                        clean=False,
                    )
                except ValidationError as exc:
                    assistant_msg = await self.messages.update(
                        assistant_msg,
                        content=self._generation_failure_message(exc.message),
                        tokens_used=0,
                        model_used="pipeline-validation",
                    )
                    await self.db.commit()
                    await self._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
                    return session, user_msg, assistant_msg, None, context_savings
                except Exception as exc:
                    assistant_msg = await self.messages.update(
                        assistant_msg,
                        content=self._generation_failure_message(f"{type(exc).__name__}: {exc}"),
                        tokens_used=0,
                        model_used="pipeline-provider-error",
                    )
                    await self.db.commit()
                    await self._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
                    return session, user_msg, assistant_msg, None, context_savings
            stats = generation.get("stats") or {}
            context_savings = {
                key: stats.get(key, context_savings.get(key, 0))
                for key in (
                    "full_context_estimated_tokens",
                    "selected_context_estimated_tokens",
                    "context_tokens_saved",
                    "context_tokens_saved_pct",
                )
            }
            assistant_msg = await self.messages.update(
                assistant_msg,
                content=self._generation_chat_message(generation),
                tokens_used=int(stats.get("input_tokens", 0) or 0)
                + int(stats.get("output_tokens", 0) or 0),
                model_used=f"generator:{generation.get('provider') or 'auto'}"[:50],
            )
            await self.db.commit()
            await self._publish_chat_event(project_id, "chat_message_updated", assistant_msg)
            return session, user_msg, assistant_msg, generation, context_savings

        # If the user attached images, append a brief textual reference so the
        # LLM at minimum knows that images were shared. Vision-capable models
        # would benefit from full image data - we leave that to a future pass.
        prompt = message
        if attachments:
            names = ", ".join(a.get("name", "image") for a in attachments)
            prompt = f"{message}\n\n[The user also attached: {names}]"

        result = await LLMService(self.db, user.id).chat(
            prompt,
            history=history,
            project_context=ctx,
            model_id=model_id,
        )
        assistant_msg = await self.messages.add(
            ChatMessage(
                session_id=session.id,
                message_type="assistant",
                content=result["text"],
                tokens_used=result["tokens"],
                model_used=result["model"][:50],
                attachments=[],
                created_at=assistant_created_at,
            )
        )
        return session, user_msg, assistant_msg, None, context_savings

    async def analyze(
        self,
        project_id: UUID,
        user: User,
        description: str,
        model_id: UUID | None,
    ) -> ProjectAnalysis:
        project = await self._ensure_owns_project(project_id, user)
        result = await LLMService(self.db, user.id).analyze(description, model_id=model_id)
        parsed: dict = result["result"] or {}

        analysis = await self.analyses.for_project(project_id)
        if analysis is None:
            analysis = ProjectAnalysis(project_id=project.id)
            self.db.add(analysis)
        analysis.requirements_summary = parsed.get("requirements_summary", "")
        analysis.technical_specifications = parsed.get("technical_specifications", "")
        analysis.database_schema = parsed.get("database_schema", "")
        analysis.api_endpoints = parsed.get("api_endpoints", []) or []
        analysis.documentation = parsed.get("documentation", "")
        await self.db.flush()
        return analysis

    async def get_analysis(self, project_id: UUID, user: User) -> ProjectAnalysis:
        await self._ensure_owns_project(project_id, user)
        analysis = await self.analyses.for_project(project_id)
        if not analysis:
            raise NotFoundError("Project has no analysis yet")
        return analysis
