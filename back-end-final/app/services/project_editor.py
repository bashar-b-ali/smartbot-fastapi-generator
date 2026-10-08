"""Edit existing generated FastAPI projects without regenerating from scratch."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import logger
from app.llm.providers import (
    get_provider,
    get_provider_from_config,
    provider_selection_metadata,
)
from app.llm.writer import WriteOutcome
from app.models.project import Project
from app.models.user import User
from app.repositories.chat import ProjectHelperRepository
from app.repositories.llm_config import LLMModelConfigRepository
from app.repositories.pipeline_memory import PipelineMemoryRepository
from app.repositories.project import ProjectFileRepository, ProjectRepository
from app.services.change_request import ChangeRequestState
from app.services.model_pipeline.editing import ModelPatchEditPipeline
from app.services.model_pipeline.intent import extract_prompt_intent
from app.services.model_pipeline.types import ModelPatchValidationError
from app.services.project_edit.artifact_validation import build_preservation_contract
from app.services.project_edit.memory import persist_requirement_contract, prepare_edit_memory
from app.services.project_edit.results import finish_edit_result, no_change_result
from app.services.project_edit.selection import select_edit_files
from app.services.project_indexer import ProjectIndexer, build_project_index


class ProjectEditService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.projects = ProjectRepository(db)
        self.project_files = ProjectFileRepository(db)
        self.helpers = ProjectHelperRepository(db)
        self.pipeline_memory = PipelineMemoryRepository(db)
        self.model_configs = LLMModelConfigRepository(db)

    async def _project(self, project_id: UUID, user: User) -> Project:
        project = await self.projects.get_for_user(project_id, user.id)
        if not project:
            raise NotFoundError("Project not found")
        if not project.folder_path:
            raise NotFoundError("Project folder not initialized")
        return project

    async def _edit_model_config(self, user: User, model_id: UUID | None) -> Any | None:
        if model_id:
            config = await self.model_configs.get_active_for_user(model_id, user.id)
            if not config:
                raise ValidationError("Model config not found or inactive")
            return config
        return None

    @staticmethod
    def _edit_provider_label(
        model_config: Any | None, provider_type: str | None = None, model: str | None = None
    ) -> str:
        if model_config:
            provider = str(getattr(model_config, "provider", "") or "custom")
            model_id = str(getattr(model_config, "model_id", "") or "")
            return f"{provider}-edit/{model_id}".rstrip("/")
        provider = provider_type or settings.llm_provider or "ollama"
        selected_model = model or settings.llm_model or ""
        return f"{provider}-edit/{selected_model}".rstrip("/")

    def _provider_for_edit(
        self, model_config: Any | None, provider_type: str | None = None, model: str | None = None
    ) -> Any:
        if model_config:
            return get_provider_from_config(model_config)
        chosen_provider = provider_type if provider_type and provider_type != "auto" else (settings.llm_provider or "ollama")
        return get_provider(
            provider_type=chosen_provider,
            model=model or settings.llm_model or None,
        )

    def _no_change_result(
        self,
        *,
        project_id: UUID,
        root: Path,
        target_paths: list[str],
        model_result: dict[str, Any],
        change_state: ChangeRequestState | None,
        reason: str,
        provider_label: str = "ollama-edit",
        pipeline_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return no_change_result(
            project_id=project_id,
            root=root,
            target_paths=target_paths,
            model_result=model_result,
            change_state=change_state,
            reason=reason,
            provider_label=provider_label,
            pipeline_trace=pipeline_trace,
        )

    async def _finish_edit_result(
        self,
        *,
        project: Project,
        project_id: UUID,
        root: Path,
        changed: list[WriteOutcome],
        summary: list[str],
        edit_plan: dict[str, Any],
        retries: int,
        provider_label: str | None = None,
        usage: dict[str, int] | None = None,
        pipeline_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await finish_edit_result(
            db=self.db,
            project_files=self.project_files,
            project=project,
            project_id=project_id,
            root=root,
            changed=changed,
            summary=summary,
            edit_plan=edit_plan,
            retries=retries,
            provider_label=provider_label,
            usage=usage,
            pipeline_trace=pipeline_trace,
            artifact_warnings_only=False,
        )

    async def _record_failed_patch_contract(
        self,
        *,
        project_id: UUID,
        prompt: str,
        error: ModelPatchValidationError,
        provider_label: str,
    ) -> None:
        patch_set = error.patch_set
        requirements = patch_set.get("requirements") if isinstance(patch_set, dict) else []
        if not isinstance(requirements, list) or not requirements:
            return
        artifact_validation = error.validation.get("artifact_validation") or {
            "passed": False,
            "missing_artifacts": [error.message],
            "regressions": [],
            "covered_artifacts": [],
        }
        validation = {
            "passed": False,
            "accepted": False,
            "errors": [error.message],
            "artifact_validation": artifact_validation,
            "missing_artifacts": artifact_validation.get("missing_artifacts") or [error.message],
            "regressions": artifact_validation.get("regressions") or [],
        }
        await persist_requirement_contract(
            self.helpers,
            project_id,
            prompt=prompt,
            requirements=requirements,
            gap={
                "covered": artifact_validation.get("covered_artifacts") or [],
                "missing": artifact_validation.get("missing_artifacts") or [error.message],
                "regressions": artifact_validation.get("regressions") or [],
                "artifact_validation": artifact_validation,
            },
            plan={
                "intent": "model_patch_edit_failed",
                "summary": patch_set.get("summary") if isinstance(patch_set, dict) else "",
                "steps": patch_set.get("patches") if isinstance(patch_set, dict) else [],
                "requirements": requirements,
                "artifact_contract": patch_set.get("artifact_contract") if isinstance(patch_set, dict) else {},
            },
            coverage={
                "passed": False,
                "covered": artifact_validation.get("covered_artifacts") or [],
                "missing": artifact_validation.get("missing_artifacts") or [error.message],
                "partial": [
                    {"requirement_id": "regression", "needed_change": item}
                    for item in (artifact_validation.get("regressions") or [])
                ],
                "artifact_validation": artifact_validation,
            },
            validation=validation,
            changed_files=[],
            stats={
                "changed_files": 0,
                "input_tokens": error.usage.input_tokens,
                "output_tokens": error.usage.output_tokens,
                "retries": error.usage.retries,
            },
            provider=provider_label,
            source="edit",
        )
        await self.db.flush()

    async def edit_project(
        self,
        project_id: UUID,
        user: User,
        *,
        prompt: str,
        change_state: ChangeRequestState | None = None,
        model_id: UUID | None = None,
        provider_type: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        project = await self._project(project_id, user)
        root = Path(project.folder_path)
        if not root.exists():
            raise NotFoundError("Project folder does not exist on disk")

        model_config = await self._edit_model_config(user, model_id)
        provider = self._provider_for_edit(model_config, provider_type, model)
        provider_label = self._edit_provider_label(model_config, provider_type, model)
        model_selection = provider_selection_metadata(
            provider,
            requested_provider=provider_type,
            requested_model=model,
            source="model_config" if model_config else "explicit" if provider_type and provider_type != "auto" else "default",
        )
        selection_prompt = (
            change_state.resolved_prompt
            if change_state and change_state.has_context
            else (change_state.current_message if change_state else prompt)
        )
        await ProjectIndexer(self.db).refresh(
            project,
            reason="pre_edit",
            write_endpoint_doc=False,
        )
        index = await asyncio.to_thread(build_project_index, root)
        helper = await self.helpers.get_or_create(project_id)
        accepted_contracts = [
            contract
            for contract in (helper.requirement_contracts or [])
            if isinstance(contract, dict) and contract.get("accepted") is True
        ]
        validation_contracts = [
            build_preservation_contract(index),
            *accepted_contracts[:10],
        ]
        raw_memory = await self.pipeline_memory.compact_context(project_id)
        prompt_intent = extract_prompt_intent(
            selection_prompt,
            existing_schema=raw_memory.get("schema") if isinstance(raw_memory.get("schema"), dict) else None,
        )
        saved_memory = prepare_edit_memory(
            raw_memory,
            current_contract=prompt_intent.get("contract") or {},
        )
        target_paths = select_edit_files(
            root,
            index,
            selection_prompt,
            requirement_contracts=accepted_contracts[:3],
        )
        if not target_paths:
            raise ValidationError("No editable project files were found for this request")

        try:
            edit_result = await asyncio.to_thread(
                ModelPatchEditPipeline(provider).edit,
                prompt=selection_prompt,
                root=root,
                index=index,
                previous_contracts=accepted_contracts[:5],
                validation_contracts=validation_contracts,
                saved_memory=saved_memory,
                selected_files=target_paths,
                model_first=True,
            )
        except ModelPatchValidationError as exc:
            await self._record_failed_patch_contract(
                project_id=project_id,
                prompt=selection_prompt,
                error=exc,
                provider_label=provider_label,
            )
            await self.pipeline_memory.save_edit_memory(
                project_id=project_id,
                prompt=selection_prompt,
                provider=provider_label,
                accepted=False,
                edit_plan=exc.patch_set if isinstance(exc.patch_set, dict) else {},
                stage_outputs=exc.stage_outputs,
                stats={
                    "input_tokens": exc.usage.input_tokens,
                    "output_tokens": exc.usage.output_tokens,
                    "retries": exc.usage.retries,
                },
            )
            return self._no_change_result(
                project_id=project_id,
                root=root,
                target_paths=exc.selected_files or target_paths,
                model_result={"error": str(exc), "requirements": exc.patch_set.get("requirements") or [], "model_selection": model_selection},
                change_state=change_state,
                reason=str(exc),
                provider_label=provider_label,
                pipeline_trace=exc.stage_outputs.get("trace") or [],
            )
        except ValidationError as exc:
            await self.pipeline_memory.save_edit_memory(
                project_id=project_id,
                prompt=selection_prompt,
                provider=provider_label,
                accepted=False,
                edit_plan={"error": str(exc)},
                stage_outputs={},
                stats={},
            )
            return self._no_change_result(
                project_id=project_id,
                root=root,
                target_paths=target_paths,
                model_result={"error": str(exc), "model_selection": model_selection},
                change_state=change_state,
                reason=str(exc),
                provider_label=provider_label,
            )
        except Exception as exc:
            logger.exception("Model patch edit failed")
            return self._no_change_result(
                project_id=project_id,
                root=root,
                target_paths=target_paths,
                model_result={"error": f"{type(exc).__name__}: {exc}", "model_selection": model_selection},
                change_state=change_state,
                reason="The edit model did not produce a valid patch set.",
                provider_label=provider_label,
            )

        if not edit_result.changed:
            return self._no_change_result(
                project_id=project_id,
                root=root,
                target_paths=target_paths,
                model_result=edit_result.edit_plan,
                change_state=change_state,
                reason="The edit model patch set produced no active file changes.",
                provider_label=provider_label,
                pipeline_trace=edit_result.stage_outputs.get("trace") or [],
            )

        summary_text = str(edit_result.edit_plan.get("summary") or "").strip()
        edit_plan = {
            **edit_result.edit_plan,
            "prompt": selection_prompt,
            "target": "",
            "conversation_state": change_state.summary if change_state else "",
            "selected_files": edit_result.selected_files,
            "requirements": edit_result.requirements,
            "artifact_contract": edit_result.artifact_contract,
        }
        edit_plan["model_selection"] = model_selection
        result = await self._finish_edit_result(
            project=project,
            project_id=project_id,
            root=root,
            changed=edit_result.changed,
            summary=([summary_text] if summary_text else [
                "Read the current project index and selected the existing files to edit.",
                "Applied the requested change as exact patch operations.",
                "Validated static code structure and explicit artifact checks before accepting the edit.",
            ]),
            edit_plan=edit_plan,
            retries=edit_result.usage.retries,
            provider_label=edit_result.provider or provider_label,
            usage={
                "input_tokens": edit_result.usage.input_tokens,
                "output_tokens": edit_result.usage.output_tokens,
            },
            pipeline_trace=edit_result.stage_outputs.get("trace") or [],
        )
        await self.pipeline_memory.save_edit_memory(
            project_id=project_id,
            prompt=selection_prompt,
            provider=str(result.get("provider") or edit_result.provider or provider_label),
            accepted=bool(result.get("accepted")),
            edit_plan=edit_plan,
            stage_outputs=edit_result.stage_outputs,
            stats=result.get("stats") or {},
        )
        artifact_validation = result.get("artifact_validation") or {}
        await persist_requirement_contract(
            self.helpers,
            project_id,
            prompt=selection_prompt,
            requirements=edit_result.requirements,
            gap={
                "covered": artifact_validation.get("covered_artifacts") or [],
                "missing": artifact_validation.get("missing_artifacts") or [],
                "regressions": artifact_validation.get("regressions") or [],
                "artifact_validation": artifact_validation,
            },
            plan=edit_plan,
            coverage={
                "passed": bool(result.get("accepted")),
                "covered": artifact_validation.get("covered_artifacts") or [],
                "missing": artifact_validation.get("missing_artifacts") or [],
                "partial": [
                    {"requirement_id": "regression", "needed_change": item}
                    for item in (artifact_validation.get("regressions") or [])
                ],
                "artifact_validation": artifact_validation,
            },
            validation=result.get("validation") or {},
            changed_files=[item.get("path", "") for item in result.get("files") or [] if item.get("path")],
            stats=result.get("stats") or {},
            provider=str(result.get("provider") or edit_result.provider or provider_label),
            source="edit",
        )
        await self.db.flush()
        return result
