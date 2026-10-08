"""Model-owned FastAPI project generation orchestration."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import logger
from app.llm.project_validator import validate_written_project
from app.llm.providers import get_provider, get_provider_from_config, provider_selection_metadata
from app.llm.writer import WriteOutcome, write_project
from app.models.project import Project, ProjectFile
from app.models.user import User
from app.repositories.chat import ProjectHelperRepository
from app.repositories.llm_config import LLMModelConfigRepository
from app.repositories.pipeline_memory import PipelineMemoryRepository
from app.repositories.project import ProjectFileRepository, ProjectRepository
from app.services.model_pipeline.generation import ModelOwnedGenerationPipeline
from app.services.model_pipeline.validation import validation_failure_reasons
from app.services.project_context import (
    PLANNER_CONTEXT_MAX_CHARS,
    build_project_context,
    context_token_savings,
)
from app.services.project_edit.artifact_validation import validate_artifacts
from app.services.project_edit.io import ensure_inside_root, safe_rel
from app.services.project_edit.memory import persist_requirement_contract
from app.services.project_indexer import ProjectIndexer, build_project_index
from app.services.runtime_validation import validate_runtime_project

_generation_locks: dict[UUID, asyncio.Lock] = {}
_generation_locks_guard = asyncio.Lock()


async def _generation_lock(project_id: UUID) -> asyncio.Lock:
    async with _generation_locks_guard:
        lock = _generation_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            _generation_locks[project_id] = lock
        return lock


def _existing_file_bytes(root: Path, files) -> dict[str, bytes | None]:
    before: dict[str, bytes | None] = {}
    for spec in files:
        target = root / spec.path
        before[spec.path] = target.read_bytes() if target.exists() and target.is_file() else None
    return before


def _changed_outcomes(root: Path, outcomes: list[WriteOutcome], before: dict[str, bytes | None]) -> list[WriteOutcome]:
    changed: list[WriteOutcome] = []
    for outcome in outcomes:
        target = root / outcome.path
        current = target.read_bytes() if target.exists() and target.is_file() else b""
        if before.get(outcome.path) != current:
            changed.append(outcome)
    return changed


class GeneratorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.projects = ProjectRepository(db)
        self.project_files = ProjectFileRepository(db)
        self.project_helpers = ProjectHelperRepository(db)
        self.pipeline_memory = PipelineMemoryRepository(db)
        self.model_configs = LLMModelConfigRepository(db)

    async def _project(self, project_id: UUID, user: User) -> Project:
        project = await self.projects.get_for_user(project_id, user.id)
        if not project:
            raise NotFoundError("Project not found")
        if not project.folder_path:
            raise NotFoundError("Project folder not initialized")
        return project

    async def _ensure_project_writable(self, project_id: UUID, user: User, root: Path) -> None:
        project = await self.projects.get_for_user(project_id, user.id)
        if not project:
            raise NotFoundError("Project was deleted before model output could be applied")
        if not project.folder_path or Path(project.folder_path) != root:
            raise NotFoundError("Project folder changed before model output could be applied")
        if not root.exists():
            raise NotFoundError("Project folder no longer exists on disk")

    async def _project_root(self, project_id: UUID, user: User) -> Path:
        project = await self._project(project_id, user)
        root = Path(project.folder_path)
        if not root.exists():
            raise NotFoundError("Project folder does not exist on disk")
        return root

    async def _project_index_context(self, project_id: UUID, prompt: str) -> tuple[str, dict]:
        helper = await self.project_helpers.for_project(project_id)
        context = build_project_context(
            helper=helper,
            max_chars=PLANNER_CONTEXT_MAX_CHARS,
            query=prompt,
        ) or ""
        saved_memory = await self.pipeline_memory.compact_context(project_id)
        if any(
            (
                saved_memory["schema"].get("ddl_sql"),
                saved_memory["api_contract"].get("contract"),
                saved_memory["file_plan"].get("plan"),
            )
        ):
            compact_memory = json.dumps(saved_memory, ensure_ascii=False, default=str)
            context = (context + "\n\nSaved pipeline memory:\n" + compact_memory)[-PLANNER_CONTEXT_MAX_CHARS:]
        savings = context_token_savings(
            helper=helper,
            max_chars=PLANNER_CONTEXT_MAX_CHARS,
            query=prompt,
        )
        return context, savings

    async def _provider_for_generation(
        self,
        user: User,
        *,
        provider: str,
        model: str | None,
        model_id: UUID | None,
    ) -> object:
        if model_id:
            config = await self.model_configs.get_active_for_user(model_id, user.id)
            if not config:
                raise ValidationError("Model config not found or inactive")
            return get_provider_from_config(config)
        chosen_provider = provider if provider != "auto" else (settings.llm_provider or "ollama")
        return get_provider(
            provider_type=chosen_provider,
            model=model or settings.llm_model or None,
        )

    async def _record_generated_files(
        self,
        *,
        project_id: UUID,
        outcomes,
        clean: bool,
    ) -> None:
        if clean:
            await self.project_files.delete_generated_for_project(project_id)
        else:
            await self.project_files.delete_paths_for_project(
                project_id, [outcome.path for outcome in outcomes]
            )
        for outcome in outcomes:
            self.db.add(
                ProjectFile(
                    project_id=project_id,
                    file_name=Path(outcome.path).name,
                    file_path=outcome.path,
                    file_size=outcome.bytes_written,
                    file_type="generated",
                )
            )
        await self.db.flush()

    async def _clean_previous_generated_files(self, project_id: UUID, root: Path) -> None:
        previous = await self.project_files.list_generated_for_project(project_id)
        parent_dirs: set[Path] = set()
        for item in previous:
            rel_path = str(item.file_path or "")
            try:
                rel = safe_rel(rel_path)
                target = ensure_inside_root(root, rel)
            except ValidationError:
                logger.warning("generator.clean_previous.refused_path", project_id=str(project_id), path=rel_path)
                continue
            if not target.exists():
                continue
            if not target.is_file():
                logger.warning("generator.clean_previous.refused_non_file", project_id=str(project_id), path=rel_path)
                continue
            try:
                await asyncio.to_thread(target.unlink)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning(
                    "generator.clean_previous.unlink_failed",
                    project_id=str(project_id),
                    path=rel_path,
                    error=str(exc),
                )
                continue
            parent_dirs.add(target.parent)

        root_resolved = root.resolve()
        for folder in sorted(parent_dirs, key=lambda path: len(path.parts), reverse=True):
            current = folder
            while current != root_resolved and root_resolved in current.parents:
                try:
                    await asyncio.to_thread(current.rmdir)
                except OSError:
                    break
                current = current.parent

    async def preview_plan(
        self,
        project_id: UUID,
        user: User,
        *,
        prompt: str,
        provider: str = "auto",
        model: str | None = None,
        model_id: UUID | None = None,
    ) -> dict:
        """Dry run the model-owned generation contract and file tree."""
        await self._project_root(project_id, user)  # ownership check
        index_context, context_savings = await self._project_index_context(project_id, prompt)
        llm_provider = await self._provider_for_generation(
            user,
            provider=provider,
            model=model,
            model_id=model_id,
        )
        result = await asyncio.to_thread(
            ModelOwnedGenerationPipeline(llm_provider).preview,
            prompt,
            existing_context=index_context,
        )

        return {
            "plan": result.plan,
            "file_paths": [f.path for f in result.files],
            "stats": {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "retries": result.usage.retries,
                "file_count": len(result.files),
                **context_savings,
            },
            "provider": result.provider,
            "selected_template": {
                "key": "model_owned",
                "name": "Model-owned generation",
                "guidance": "The model plans schema, API contract, and file structure before code generation.",
                "resources": [],
                "endpoint_pattern": "",
                "editable_files": [f.path for f in result.files],
                "token_strategy": "saved schema/API memory plus staged model calls",
            },
            "validation": {
                "passed": True,
                "prompt_contract": {
                    "passed": True,
                    "expected": {
                        "requirements": [item["id"] for item in result.requirements],
                        "file_paths": [f.path for f in result.files],
                    },
                },
            },
        }

    async def generate_from_prompt(
        self,
        project_id: UUID,
        user: User,
        *,
        prompt: str,
        provider: str = "auto",
        model: str | None = None,
        model_id: UUID | None = None,
        clean: bool = True,
    ) -> dict:
        total_started = perf_counter()
        stage_timings_ms: dict[str, float] = {}
        project = await self._project(project_id, user)
        root = Path(project.folder_path)
        if not root.exists():
            raise NotFoundError("Project folder does not exist on disk")
        context_started = perf_counter()
        index_context, context_savings = await self._project_index_context(project_id, prompt)
        stage_timings_ms["context_selection"] = round((perf_counter() - context_started) * 1000, 2)
        helper = await self.project_helpers.get_or_create(project_id)
        previous_contracts = list(helper.requirement_contracts or [])
        llm_provider = await self._provider_for_generation(
            user,
            provider=provider,
            model=model,
            model_id=model_id,
        )
        model_selection = provider_selection_metadata(
            llm_provider,
            requested_provider=provider,
            requested_model=model,
            source="model_config" if model_id else "explicit" if provider != "auto" else "default",
        )

        planning_started = perf_counter()
        result = await asyncio.to_thread(
            ModelOwnedGenerationPipeline(llm_provider).build,
            prompt,
            existing_context=index_context,
            model_first=True,
        )
        stage_timings_ms["planning"] = round((perf_counter() - planning_started) * 1000, 2)

        validation_total_ms = 0.0
        build_started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="fastapi_generation_") as temp_dir:
            temp_root = Path(temp_dir)
            temp_outcomes = await write_project(temp_root, result.files)
            stage_timings_ms["implementation"] = round((perf_counter() - build_started) * 1000, 2)
            validation_started = perf_counter()
            validation = validate_written_project(temp_root, temp_outcomes)
            validation_dict = validation.as_dict()
            validation_dict["prompt_contract"] = {
                "passed": True,
                "expected": {
                    "requirements": [item["id"] for item in result.requirements],
                    "file_paths": [item.path for item in result.files],
                },
            }
            temp_index = build_project_index(temp_root)
            artifact_validation = validate_artifacts(
                temp_index,
                result.artifact_contract,
                previous_contracts=previous_contracts,
            ).as_dict()
            runtime_validation = validate_runtime_project(temp_root)
            validation_total_ms += (perf_counter() - validation_started) * 1000
            validation_dict["artifact_validation"] = artifact_validation
            validation_dict["requirements"] = {
                "findings": artifact_validation.get("requirement_findings") or [],
                "satisfied": len(artifact_validation.get("covered_artifacts") or []),
                "gaps": len(artifact_validation.get("missing_artifacts") or []) + len(artifact_validation.get("regressions") or []),
            }
            validation_dict["runtime_validation"] = runtime_validation
            validation_dict["missing_artifacts"] = artifact_validation.get("missing_artifacts") or []
            validation_dict["regressions"] = artifact_validation.get("regressions") or []
            static_safe = bool(validation.passed)
            # Static safety is the write boundary. Contract/runtime gaps are repairable warnings.
            write_allowed = bool(static_safe and result.files)
            validation_dict["warnings"] = [
                *(artifact_validation.get("missing_artifacts") or []),
                *(artifact_validation.get("regressions") or []),
                *(runtime_validation.get("errors") or []),
                *(runtime_validation.get("warnings") or []),
            ]
            validation_dict["static_safe"] = static_safe
            validation_dict["write_allowed"] = write_allowed
            validation_dict["accepted"] = write_allowed
            validation_dict["passed"] = bool(validation_dict["accepted"])
            validation_dict["pipeline_state"] = (
                "accepted"
                if validation_dict["accepted"] and not validation_dict["warnings"]
                else "written_with_warnings"
                if validation_dict["accepted"]
                else "rejected_before_write"
            )
            failure_reasons = validation_failure_reasons(validation_dict, artifact_validation)
            if not runtime_validation.get("passed"):
                categories = list(failure_reasons.get("categories") or [])
                if "runtime_validation" not in categories:
                    categories.append("runtime_validation")
                failure_reasons = {**failure_reasons, "primary": "runtime_validation", "categories": categories}
            validation_dict["failure_reasons"] = failure_reasons
            validation_dict["failure_category"] = "" if validation_dict["accepted"] else failure_reasons["primary"]
            canonical_spec = result.stage_outputs.get("canonical_spec") or result.plan.get("canonical_spec") or {}
            spec_summary = canonical_spec.get("summary") or {}
            generation_strategy = str(result.stage_outputs.get("generation_strategy") or "model_codegen_targeted_repair")
            drift_rejections = result.stage_outputs.get("drift_rejections") or canonical_spec.get("drift_rejections") or []
            repair_plan = result.stage_outputs.get("repair_plan") or {}
            next_action = (
                "written_to_project"
                if validation_dict["accepted"]
                else "fix_renderer_or_targeted_files_from_repair_plan"
                if generation_strategy.startswith("renderer_fast_path")
                else "inspect_repair_plan_and_retry_affected_files"
            )
            validation_dict["spec_summary"] = spec_summary
            validation_dict["generation_strategy"] = generation_strategy
            validation_dict["drift_rejections"] = drift_rejections
            validation_dict["repair_plan"] = repair_plan
            validation_dict["next_action"] = next_action
            validation_dict["model_generation"] = {
                "candidate_files": [item.path for item in result.files],
                "repair_attempts": (result.stage_outputs.get("repair") or [])[:3],
                "final_candidate_accepted": bool(
                    (result.stage_outputs.get("validation") or {}).get("accepted")
                ),
                "trace": result.stage_outputs.get("trace") or [],
                "generation_strategy": generation_strategy,
                "spec_summary": spec_summary,
                "drift_rejections": drift_rejections,
                "repair_plan": repair_plan,
                "runtime_validation": runtime_validation,
                "next_action": next_action,
            }

            if not write_allowed:
                outcomes: list[WriteOutcome] = []
                changed_outcomes: list[WriteOutcome] = []
            else:
                apply_lock = await _generation_lock(project_id)
                async with apply_lock:
                    await self._ensure_project_writable(project_id, user, root)
                    before = _existing_file_bytes(root, result.files)
                    if clean:
                        await self._clean_previous_generated_files(project_id, root)
                    outcomes = await write_project(root, result.files)
                    await self._record_generated_files(project_id=project_id, outcomes=outcomes, clean=clean)
                    changed_outcomes = _changed_outcomes(root, outcomes, before)
                    index = await ProjectIndexer(self.db).refresh(project, reason="generation")
                endpoint_doc = index.get("endpoint_doc") or {}
                if endpoint_doc.get("changed"):
                    changed_outcomes.append(
                        WriteOutcome(
                            path=endpoint_doc["path"],
                            bytes_written=int(endpoint_doc.get("bytes_written", 0) or 0),
                        )
                    )

        stage_timings_ms["validation"] = round(validation_total_ms, 2)
        stage_timings_ms["total"] = round((perf_counter() - total_started) * 1000, 2)
        stats = {
            "total_files": len(outcomes),
            "changed_files": len(changed_outcomes),
            "total_bytes": sum(o.bytes_written for o in outcomes),
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "retries": result.usage.retries,
            "stage_timings_ms": stage_timings_ms,
            **context_savings,
        }
        await persist_requirement_contract(
            self.project_helpers,
            project_id,
            prompt=prompt,
            requirements=result.requirements,
            gap={},
            plan=result.plan,
            coverage={
                "passed": bool(validation_dict.get("accepted")),
                "covered": (validation_dict.get("artifact_validation") or {}).get("covered_artifacts") or [],
                "missing": validation_dict.get("missing_artifacts") or [],
                "partial": [],
            },
            validation=validation_dict,
            changed_files=[outcome.path for outcome in changed_outcomes],
            stats=stats,
            provider=result.provider,
            source="generation",
        )
        await self.pipeline_memory.save_generation_memory(
            project_id=project_id,
            prompt=prompt,
            provider=result.provider,
            accepted=bool(validation_dict.get("accepted")),
            plan=result.plan,
            stage_outputs=result.stage_outputs,
            stats=stats,
        )
        first_file = outcomes[0].path if outcomes else ""
        first_changed_file = changed_outcomes[0].path if changed_outcomes else first_file

        logger.info(
            "generator.generate_from_prompt project_id=%s files=%d provider=%s validation=%s tokens=%d/%d",
            str(project_id),
            len(outcomes),
            result.provider,
            validation_dict["passed"],
            result.usage.input_tokens,
            result.usage.output_tokens,
        )

        return {
            "project_id": str(project_id),
            "project_root": str(root),
            "plan": result.plan,
            "files": [
                {"path": o.path, "bytes_written": o.bytes_written} for o in changed_outcomes
            ],
            "all_files": [
                {"path": o.path, "bytes_written": o.bytes_written} for o in outcomes
            ],
            "stats": stats,
            "provider": result.provider,
            "model_selection": model_selection,
            "selected_template": {
                "key": "model_owned",
                "name": "Model-owned generation",
                "guidance": "The pipeline plans schema/API, then uses the deterministic renderer when the canonical spec is covered; otherwise it uses targeted model codegen.",
                "resources": [],
                "endpoint_pattern": "",
                "editable_files": [item.path for item in result.files],
                "token_strategy": generation_strategy,
            },
            "validation": validation_dict,
            "artifact_validation": validation_dict.get("artifact_validation") or {},
            "runtime_validation": validation_dict.get("runtime_validation") or {},
            "pipeline_trace": validation_dict["model_generation"].get("trace") or [],
            "accepted": bool(validation_dict.get("accepted")),
            "pipeline_state": validation_dict.get("pipeline_state") or "unknown",
            "failure_category": validation_dict.get("failure_category") or "",
            "missing_artifacts": validation_dict.get("missing_artifacts") or [],
            "regressions": validation_dict.get("regressions") or [],
            "stage_timings_ms": stage_timings_ms,
            "generation_strategy": generation_strategy,
            "spec_summary": spec_summary,
            "drift_rejections": drift_rejections,
            "repair_plan": repair_plan,
            "next_action": next_action,
            "context_selection": {
                "source": "project_index",
                "has_existing_context": bool(index_context),
                **context_savings,
            },
            "working_summary": (
                [
                    f"Built and accepted FastAPI project files using {generation_strategy}.",
                    f"Changed {len(changed_outcomes)} files in the project folder.",
                    "Requirement validation passed.",
                ]
                if validation_dict.get("accepted")
                else [
                    f"Generation used {generation_strategy}, but validation rejected the candidate before writing.",
                    f"Changed {len(changed_outcomes)} files in the project folder.",
                    f"Primary issue: {validation_dict.get('failure_category') or 'unknown'}.",
                    f"Next action: {next_action}.",
                ]
            ),
            "next_prompt": (
                f"Do you want to view `{first_changed_file}`?"
                if first_changed_file
                else "No file was written to view."
            ),
            "view_file_path": first_changed_file,
        }
