from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.llm.project_validator import validate_written_project
from app.llm.writer import WriteOutcome
from app.models.project import Project, ProjectFile
from app.repositories.chat import ProjectHelperRepository
from app.repositories.project import ProjectFileRepository
from app.services.change_request import ChangeRequestState
from app.services.project_edit.artifact_validation import (
    normalize_requirement_contract,
    validate_artifacts,
)
from app.services.project_edit.model_io import model_item_debug, model_result_debug
from app.services.project_indexer import ProjectIndexer
from app.services.runtime_validation import validate_runtime_project


def no_change_result(
    *,
    project_id: UUID,
    root: Path,
    target_paths: list[str],
    model_result: dict[str, Any],
    change_state: ChangeRequestState | None,
    reason: str,
    provider_label: str = "ollama-edit",
    pipeline_trace: list[dict[str, Any]] | None = None,
    artifact_warnings_only: bool = False,
) -> dict[str, Any]:
    missing = model_result.get("missing_info") if isinstance(model_result, dict) else None
    keys = sorted(model_result.keys()) if isinstance(model_result, dict) else []
    details = []
    if missing:
        details.append(f"Model missing_info: {missing}")
    if keys:
        details.append(f"Model JSON keys: {', '.join(keys)}")
    item_debug = model_item_debug(model_result) if isinstance(model_result, dict) else []
    result_debug = model_result_debug(model_result) if isinstance(model_result, dict) else {}
    if item_debug:
        details.append(f"Model file items: {item_debug}")
    if result_debug.get("message"):
        details.append(f"Model message: {result_debug['message']}")
    if result_debug.get("status"):
        details.append(f"Model status: {result_debug['status']}")
    details.append(f"Selected files: {', '.join(target_paths) or 'none'}")
    logger.warning(
        "Edit model produced no applicable changes",
        extra={
            "project_id": str(project_id),
            "target_paths": target_paths,
            "model_keys": keys,
            "model_file_items": item_debug,
            "reason": reason,
        },
    )
    return {
        "project_id": str(project_id),
        "project_root": str(root),
        "files": [],
        "stats": {
            "changed_files": 0,
            "total_bytes": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "retries": 1,
        },
        "provider": provider_label,
        "edit_plan": {
            "intent": "llm_edit_no_change",
            "target": "",
            "conversation_state": change_state.summary if change_state else "",
            "selected_files": target_paths,
            "model_keys": keys,
            "model_file_items": item_debug,
            "model_result": result_debug,
        },
        "validation": {
            "passed": False,
            "accepted": False,
            "checks": [],
            "missing_required": [],
            "errors": [reason],
            "model_result": result_debug,
            "artifact_validation": {
                "passed": False,
                "missing_artifacts": [reason],
                "regressions": [],
            },
            "missing_artifacts": [reason],
            "regressions": [],
            "model_edit": {
                "selected_files": target_paths,
                "trace": pipeline_trace or [],
            },
        },
        "artifact_validation": {
            "passed": False,
            "missing_artifacts": [reason],
            "regressions": [],
        },
        "pipeline_trace": pipeline_trace or [],
        "accepted": False,
        "pipeline_state": "edit_no_change",
        "failure_category": "no_change",
        "missing_artifacts": [reason],
        "regressions": [],
        "stage_timings_ms": {},
        "working_summary": [
            "The edit model did not return an applicable file change, so no project files were modified.",
            *details[:5],
        ],
        "next_prompt": (
            "Try again with the exact method/path and expected response, or switch to a stronger model for editing."
        ),
        "view_file_path": target_paths[0] if target_paths else "",
    }


async def finish_edit_result(
    *,
    db: AsyncSession,
    project_files: ProjectFileRepository,
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
    artifact_warnings_only: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    deleted_paths = [
        str(path)
        for path in (edit_plan.get("deleted_files") or [])
        if isinstance(path, str) and path
    ]
    await project_files.delete_paths_for_project(
        project_id,
        [item.path for item in changed] + deleted_paths,
    )
    for item in changed:
        db.add(
            ProjectFile(
                project_id=project_id,
                file_name=Path(item.path).name,
                file_path=item.path,
                file_size=item.bytes_written,
                file_type="generated",
            )
        )
    await db.flush()

    index = await ProjectIndexer(db).refresh(project, reason="edit")
    endpoint_doc = index.get("endpoint_doc") or {}
    if endpoint_doc.get("changed"):
        changed.append(
            WriteOutcome(
                path=endpoint_doc["path"],
                bytes_written=int(endpoint_doc.get("bytes_written", 0) or 0),
            )
        )

    validation_started = perf_counter()
    validation_dict = validate_written_project(root, changed).as_dict()
    helper = await ProjectHelperRepository(db).get_or_create(project_id)
    previous_contracts = list(helper.requirement_contracts or [])
    artifact_contract = normalize_requirement_contract(
        edit_plan.get("artifact_contract") if isinstance(edit_plan.get("artifact_contract"), dict) else None,
        prompt=str(edit_plan.get("prompt") or edit_plan.get("conversation_state") or ""),
        requirements=edit_plan.get("requirements") if isinstance(edit_plan.get("requirements"), list) else [],
        prompt_contract=edit_plan.get("prompt_contract") if isinstance(edit_plan.get("prompt_contract"), dict) else None,
        edit_plan=edit_plan,
        source="edit",
    )
    artifact_validation = validate_artifacts(
        index,
        artifact_contract,
        previous_contracts=previous_contracts,
    ).as_dict()
    coverage = edit_plan.get("requirement_coverage") if isinstance(edit_plan, dict) else None
    if isinstance(coverage, dict):
        validation_dict["requirement_coverage"] = coverage
        coverage["artifact_validation_passed"] = bool(artifact_validation.get("passed"))
        if artifact_validation.get("missing_artifacts"):
            coverage["passed"] = False
    validation_dict["artifact_validation"] = artifact_validation
    validation_dict["requirements"] = {
        "findings": artifact_validation.get("requirement_findings") or [],
        "satisfied": len(artifact_validation.get("covered_artifacts") or []),
        "gaps": len(artifact_validation.get("missing_artifacts") or []) + len(artifact_validation.get("regressions") or []),
    }
    runtime_validation = validate_runtime_project(root)
    validation_dict["runtime_validation"] = runtime_validation
    validation_dict["missing_artifacts"] = artifact_validation.get("missing_artifacts") or []
    validation_dict["regressions"] = artifact_validation.get("regressions") or []
    validation_dict["model_edit"] = {
        "selected_files": edit_plan.get("selected_files") or edit_plan.get("target_files") or [],
        "trace": pipeline_trace or [],
    }
    has_code_change = any(
        item.path.endswith(".py") and item.path != "API_ENDPOINTS.md"
        for item in changed
    ) or bool(deleted_paths)
    allow_noop = bool(edit_plan.get("read_only") or edit_plan.get("noop_ok"))
    # Static safety is the hard boundary; other gaps remain repairable warnings.
    accepted = bool(validation_dict.get("passed")) and has_code_change
    validation_dict["warnings"] = [
        *(artifact_validation.get("missing_artifacts") or []),
        *(artifact_validation.get("regressions") or []),
        *(runtime_validation.get("errors") or []),
        *(runtime_validation.get("warnings") or []),
        *([str(edit_plan.get("partial_failure"))] if edit_plan.get("partial_failure") else []),
    ]
    if not has_code_change and not allow_noop and (
        artifact_contract.get("requirements") or any(artifact_contract.get(key) for key in (
            "required_routes",
            "required_tables",
            "required_fields",
            "required_filters",
            "required_relationships",
            "required_behaviors",
            "removed_artifacts",
        ))
    ):
        accepted = False
        validation_dict.setdefault("errors", [])
        validation_dict["errors"].append(
            "No source files changed, so this edit was not accepted as a completed mutation."
        )
        artifact_validation.setdefault("missing_artifacts", [])
        artifact_validation["missing_artifacts"].append(
            "No source files changed for a mutation request."
        )
        artifact_validation["passed"] = False
    validation_dict["accepted"] = accepted
    validation_dict["passed"] = accepted
    validation_dict["pipeline_state"] = (
        "accepted"
        if accepted
        else "edit_no_change"
        if not has_code_change and not allow_noop
        else "draft_written"
    )
    validation_dict["failure_category"] = (
        ""
        if accepted
        else "no_change"
        if validation_dict["pipeline_state"] == "edit_no_change"
        else "regression"
        if artifact_validation.get("regressions")
        else "missing_artifact"
        if artifact_validation.get("missing_artifacts")
        else "runtime_validation"
        if not runtime_validation.get("passed")
        else "validation_failed"
    )
    validation_dict["artifact_validation"] = artifact_validation
    validation_dict["missing_artifacts"] = artifact_validation.get("missing_artifacts") or []
    validation_dict["regressions"] = artifact_validation.get("regressions") or []
    response_files = [{"path": item.path, "bytes_written": item.bytes_written} for item in changed]
    response_files.extend(
        {"path": path, "bytes_written": 0, "deleted": True}
        for path in deleted_paths
    )
    first = changed[0].path if changed else (deleted_paths[0] if deleted_paths else "")
    stage_timings_ms = {
        "validation": round((perf_counter() - validation_started) * 1000, 2),
        "total": round((perf_counter() - started) * 1000, 2),
    }
    return {
        "project_id": str(project_id),
        "project_root": str(root),
        "files": response_files,
        "stats": {
            "changed_files": len(response_files),
            "total_bytes": sum(item.bytes_written for item in changed),
            "input_tokens": int((usage or {}).get("input_tokens", 0) or 0),
            "output_tokens": int((usage or {}).get("output_tokens", 0) or 0),
            "retries": retries,
            "full_context_estimated_tokens": int((usage or {}).get("full_context_estimated_tokens", 0) or 0),
            "selected_context_estimated_tokens": int((usage or {}).get("selected_context_estimated_tokens", 0) or 0),
            "context_tokens_saved": int((usage or {}).get("context_tokens_saved", 0) or 0),
            "context_tokens_saved_pct": float((usage or {}).get("context_tokens_saved_pct", 0.0) or 0.0),
        },
        "provider": provider_label or "model-edit",
        "edit_plan": edit_plan,
        "validation": validation_dict,
        "artifact_validation": artifact_validation,
        "runtime_validation": runtime_validation,
        "pipeline_trace": pipeline_trace or [],
        "accepted": accepted,
        "pipeline_state": validation_dict["pipeline_state"],
        "failure_category": validation_dict["failure_category"],
        "missing_artifacts": artifact_validation.get("missing_artifacts") or [],
        "regressions": artifact_validation.get("regressions") or [],
        "stage_timings_ms": stage_timings_ms,
        "context_selection": {
            "selected_files": edit_plan.get("selected_files") or edit_plan.get("target_files") or [],
            "artifact_files": [
                item.get("path")
                for item in response_files
                if item.get("path") and item.get("path") != "API_ENDPOINTS.md"
            ],
        },
        "working_summary": summary[:4],
        "next_prompt": f"Do you want to view `{first}`?" if first else "No file was written to view.",
        "view_file_path": first,
    }
