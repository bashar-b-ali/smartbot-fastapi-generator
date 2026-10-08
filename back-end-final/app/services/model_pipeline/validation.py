from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from app.llm.file_spec import FileSpec
from app.llm.project_validator import validate_written_project
from app.llm.writer import WriteOutcome
from app.services.project_edit.artifact_validation import validate_artifacts
from app.services.project_indexer import build_project_index
from app.services.runtime_validation import validate_runtime_project


def _missing_artifact_categories(missing_artifacts: list[str]) -> list[str]:
    categories: list[str] = []
    checks = (
        ("missing_table", "missing table"),
        ("missing_field", "missing field"),
        ("missing_route", "missing route"),
        ("missing_filter", "missing filter"),
        ("missing_relationship", "missing relationship"),
        ("missing_behavior", "missing behavior"),
    )
    joined = "\n".join(missing_artifacts).lower()
    for category, marker in checks:
        if marker in joined:
            categories.append(category)
    return categories


def _static_categories(static_validation: dict[str, Any]) -> list[str]:
    categories: list[str] = []
    if static_validation.get("missing_required"):
        categories.append("missing_required_file")
    errors = " ".join(
        str(check.get("error") or "")
        for check in static_validation.get("checks") or []
        if isinstance(check, dict) and not check.get("passed")
    ).lower()
    checks = (
        ("static_role_error", "file contains route handlers"),
        ("static_role_error", "instantiates fastapi application"),
        ("placeholder_code", "placeholder"),
        ("placeholder_code", "pass/todo"),
        ("undefined_reference", "undefined names"),
        ("syntax_error", "syntax"),
        ("unresolved_import", "import"),
        ("route_wiring", "not included"),
    )
    for category, marker in checks:
        if marker in errors and category not in categories:
            categories.append(category)
    return categories


def _detected_class_names(artifact_validation: dict[str, Any]) -> set[str]:
    detected = artifact_validation.get("detected_artifacts") or {}
    classes = detected.get("classes") if isinstance(detected, dict) else []
    names: set[str] = set()
    for item in classes or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _detect_pydantic_only_model_gap(artifact_validation: dict[str, Any]) -> bool:
    missing_tables = {
        match.group(1)
        for item in artifact_validation.get("missing_artifacts") or []
        if (match := re.search(r"missing table\s+([A-Za-z_][\w]*)", str(item)))
    }
    return bool(missing_tables & _detected_class_names(artifact_validation))


def validation_failure_reasons(static_validation: dict[str, Any], artifact_validation: dict[str, Any]) -> dict[str, Any]:
    categories: list[str] = []
    categories.extend(_static_categories(static_validation))
    categories.extend(_missing_artifact_categories(artifact_validation.get("missing_artifacts") or []))
    if artifact_validation.get("regressions"):
        categories.append("regression")
    if _detect_pydantic_only_model_gap(artifact_validation):
        categories.append("pydantic_only_model")
    deduped = list(dict.fromkeys(categories))
    primary = "accepted" if static_validation.get("passed") and artifact_validation.get("passed") else (
        deduped[0] if deduped else "validation_failed"
    )
    return {
        "primary": primary,
        "categories": deduped,
        "missing_required_count": len(static_validation.get("missing_required") or []),
        "missing_artifact_count": len(artifact_validation.get("missing_artifacts") or []),
        "regression_count": len(artifact_validation.get("regressions") or []),
    }


def candidate_quality_gate(
    files: list[FileSpec],
    validation: dict[str, Any],
    artifact_contract: dict[str, Any],
) -> dict[str, Any]:
    required_tables = artifact_contract.get("required_tables") or artifact_contract.get("tables") or []
    needs_tables = bool(required_tables)
    issues: list[dict[str, Any]] = []
    failure_reasons = validation.get("failure_reasons") or {}
    categories = set(failure_reasons.get("categories") or [])

    if categories & {"static_role_error", "placeholder_code", "undefined_reference", "syntax_error"}:
        issues.append({"category": "static_blocker", "detail": sorted(categories)})
    if "pydantic_only_model" in categories and needs_tables:
        issues.append(
            {
                "category": "pydantic_only_model",
                "detail": "candidate defines requested model names but no persistence tables",
            }
        )
    if needs_tables and not (validation.get("artifact_validation") or {}).get("detected_artifacts", {}).get("tables"):
        issues.append({"category": "no_detected_tables", "detail": "database-backed contract has no detected tables"})
    if not files:
        issues.append({"category": "empty_candidate", "detail": "no generated files"})

    return {
        "passed": not issues,
        "blocked": bool(issues),
        "issues": issues,
        "failure_reasons": failure_reasons,
    }


def _validation_payload(
    static_validation: dict[str, Any],
    artifact_validation: dict[str, Any],
    runtime_validation: dict[str, Any],
) -> dict[str, Any]:
    failure_reasons = validation_failure_reasons(static_validation, artifact_validation)
    if not runtime_validation.get("passed"):
        categories = list(failure_reasons.get("categories") or [])
        if "runtime_validation" not in categories:
            categories.append("runtime_validation")
        failure_reasons = {**failure_reasons, "primary": "runtime_validation", "categories": categories}
    runtime_environment_ready = runtime_validation.get("validation_environment_ready", True)
    runtime_acceptable = bool(runtime_validation.get("passed")) or runtime_environment_ready is False
    requirement_gaps = [
        *list(artifact_validation.get("missing_artifacts") or []),
        *list(artifact_validation.get("regressions") or []),
    ]
    safety_passed = bool(static_validation.get("passed")) and not bool(artifact_validation.get("regressions"))
    return {
        "static": static_validation,
        "artifact_validation": artifact_validation,
        "static_safe": bool(static_validation.get("passed")),
        "safety_passed": safety_passed,
        "runtime_validation": runtime_validation,
        "runtime_environment_ready": runtime_environment_ready,
        "requirements": {
            "satisfied": len(artifact_validation.get("covered_artifacts") or []),
            "gaps": requirement_gaps,
            "gap_count": len(requirement_gaps),
            "state": "satisfied" if not requirement_gaps else "partial",
        },
        "repairable": bool(safety_passed and requirement_gaps),
        "accepted": bool(
            static_validation.get("passed")
            and artifact_validation.get("passed")
            and runtime_acceptable
        ),
        "failure_category": failure_reasons["primary"],
        "failure_reasons": failure_reasons,
    }


def validate_file_specs_batch(
    files: list[FileSpec],
    artifact_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not artifact_contracts:
        return []
    with tempfile.TemporaryDirectory(prefix="fastapi_model_validate_") as temp_dir:
        root = Path(temp_dir)
        outcomes: list[WriteOutcome] = []
        for spec in files:
            target = root / spec.path
            target.parent.mkdir(parents=True, exist_ok=True)
            normalized = spec.content.replace("\r\n", "\n")
            target.write_text(normalized, encoding="utf-8", newline="\n")
            outcomes.append(WriteOutcome(path=spec.path, bytes_written=len(normalized.encode("utf-8"))))
        static_validation = validate_written_project(root, outcomes).as_dict()
        index = build_project_index(root)
        runtime_validation = validate_runtime_project(root)
        return [
            _validation_payload(
                static_validation,
                validate_artifacts(index, artifact_contract).as_dict(),
                runtime_validation,
            )
            for artifact_contract in artifact_contracts
        ]


def validate_file_specs(files: list[FileSpec], artifact_contract: dict[str, Any]) -> dict[str, Any]:
    return validate_file_specs_batch(files, [artifact_contract])[0]


def candidate_validation_score(validation: dict[str, Any]) -> tuple[int, int, int, int]:
    static = validation.get("static") or {}
    artifact = validation.get("artifact_validation") or {}
    return (
        1 if static.get("passed") else 0,
        len(artifact.get("covered_artifacts") or []),
        -len(artifact.get("missing_artifacts") or []),
        -len(artifact.get("regressions") or []),
    )


def better_candidate(
    current_files: list[FileSpec],
    current_validation: dict[str, Any],
    best_files: list[FileSpec],
    best_validation: dict[str, Any],
) -> bool:
    if not current_files:
        return False
    return candidate_validation_score(current_validation) > candidate_validation_score(best_validation)
