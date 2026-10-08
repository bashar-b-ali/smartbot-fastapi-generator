from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.services.project_edit.artifact_validation import normalize_requirement_contract


def prepare_edit_memory(
    memory: dict[str, Any] | None,
    *,
    current_contract: dict[str, Any],
) -> dict[str, Any]:
    source = memory if isinstance(memory, dict) else {}
    schema_required = any(
        current_contract.get(key)
        for key in (
            "required_tables",
            "required_fields",
            "required_relationships",
        )
    )
    return {
        "schema": dict(source.get("schema") or {}) if schema_required else {},
        "api_contract": {},
        "file_plan": {},
        "context_policy": {
            "current_request_first": True,
            "schema_memory_included": schema_required,
            "stale_api_memory_removed": True,
            "stale_file_plan_removed": True,
        },
    }


async def persist_requirement_contract(
    helper_repository: Any,
    project_id: UUID,
    *,
    prompt: str,
    requirements: list[dict[str, Any]],
    gap: dict[str, Any],
    plan: dict[str, Any],
    coverage: dict[str, Any],
    validation: dict[str, Any],
    changed_files: list[str],
    stats: dict[str, Any],
    provider: str,
    source: str,
) -> None:
    helper = await helper_repository.get_or_create(project_id)
    existing = list(helper.requirement_contracts or [])
    artifact_validation = validation.get("artifact_validation") or {}
    accepted = bool(validation.get("accepted")) and bool(artifact_validation.get("passed"))
    artifact_contract = normalize_requirement_contract(
        plan.get("artifact_contract") if isinstance(plan.get("artifact_contract"), dict) else None,
        prompt=prompt,
        requirements=requirements,
        prompt_contract=plan.get("prompt_contract") if isinstance(plan.get("prompt_contract"), dict) else None,
        edit_plan=plan,
        source=source,
    )
    contract = {
        "id": uuid.uuid4().hex,
        "source": source,
        "provider": provider,
        "prompt": prompt,
        "requirements": requirements,
        **{
            key: artifact_contract.get(key) or []
            for key in (
                "required_routes",
                "required_tables",
                "required_fields",
                "required_filters",
                "required_relationships",
                "required_behaviors",
                "removed_artifacts",
            )
        },
        "gap_analysis": gap,
        "plan": {
            "intent": plan.get("intent"),
            "summary": plan.get("summary"),
            "steps": plan.get("steps") or [],
        },
        "coverage": coverage,
        "artifact_validation": artifact_validation,
        "accepted": accepted,
        "validation": {
            "passed": validation.get("passed"),
            "accepted": validation.get("accepted"),
            "errors": (validation.get("errors") or [])[:10],
            "missing_required": (validation.get("missing_required") or [])[:10],
            "artifact_validation": artifact_validation,
        },
        "changed_files": changed_files,
        "stats": stats,
    }
    if accepted:
        helper.requirement_contracts = ([contract] + existing)[:20]
    helper.recent_changes = ([
        {
            "kind": "requirement_contract",
            "contract_id": contract["id"],
            "source": source,
            "requirements": len(requirements),
            "coverage_passed": bool(coverage.get("passed")),
            "validation_passed": bool(validation.get("passed")),
            "accepted": accepted,
            "missing_artifacts": (artifact_validation.get("missing_artifacts") or [])[:8],
            "regressions": (artifact_validation.get("regressions") or [])[:8],
            "changed_files": changed_files[:12],
        }
    ] + list(helper.recent_changes or []))[:20]
