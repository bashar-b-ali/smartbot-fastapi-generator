from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.services.model_pipeline.artifacts import normalize_artifact_contract
from app.services.model_pipeline.naming import identifier, plural

CapabilityOwner = Literal["renderer", "bot", "mixed"]


@dataclass(frozen=True)
class Capability:
    capability_id: str
    owner: CapabilityOwner
    required_files: tuple[str, ...] = ()
    required_contract_fields: tuple[str, ...] = ()
    validation_checks: tuple[str, ...] = ()
    deterministic_repairs: tuple[str, ...] = ()
    unsupported_examples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITY_REGISTRY: dict[str, Capability] = {
    "crud_resource": Capability(
        "crud_resource",
        "renderer",
        ("models.py", "schemas.py", "routers/{resource}.py", "main.py"),
        ("required_tables", "required_routes"),
        ("table_exists", "route_exists", "router_wired", "runtime_import"),
        ("missing_table", "missing_route_wiring"),
    ),
    "list_filter": Capability(
        "list_filter",
        "renderer",
        ("models.py", "routers/{resource}.py"),
        ("required_fields", "required_filters"),
        ("field_exists", "query_filter_exists"),
        ("missing_field", "missing_filter"),
    ),
    "sqlmodel_relationship": Capability(
        "sqlmodel_relationship",
        "renderer",
        ("models.py", "schemas.py"),
        ("required_relationships",),
        ("foreign_key_exists", "related_table_exists"),
        ("missing_relationship", "missing_table"),
    ),
    "jwt_auth_basic": Capability(
        "jwt_auth_basic",
        "renderer",
        ("auth.py", "models.py", "routers/auth.py", "main.py"),
        ("required_behaviors", "required_tables", "required_routes"),
        ("auth_dependency_exists", "token_route_exists", "runtime_import"),
        ("missing_auth_dependency", "missing_route_wiring"),
    ),
    "roles_basic": Capability(
        "roles_basic",
        "mixed",
        ("models.py", "auth.py"),
        ("required_fields", "required_behaviors"),
        ("role_field_exists", "role_check_exists"),
        ("missing_field", "missing_auth_dependency"),
    ),
    "admin_only_actions": Capability(
        "admin_only_actions",
        "mixed",
        ("auth.py", "routers/{resource}.py"),
        ("required_behaviors", "required_routes"),
        ("admin_dependency_exists", "protected_route_exists"),
        ("missing_auth_dependency", "missing_route"),
    ),
    "task_assignment": Capability(
        "task_assignment",
        "mixed",
        ("models.py", "schemas.py", "routers/tasks.py"),
        ("required_tables", "required_fields", "required_relationships"),
        ("user_task_relationship_exists", "assignment_field_exists"),
        ("missing_table", "missing_relationship", "missing_field"),
    ),
    "generic_file_upload_download": Capability(
        "generic_file_upload_download",
        "renderer",
        ("files.py", "main.py"),
        ("required_behaviors", "required_routes"),
        ("upload_route_exists", "download_route_exists"),
        ("missing_route", "missing_route_wiring"),
    ),
    "scoped_user_download": Capability(
        "scoped_user_download",
        "renderer",
        ("routers/{resource}.py", "auth.py"),
        ("custom_routes", "required_behaviors"),
        ("current_user_dependency", "scoped_filter", "file_response"),
        (),
        ("generic /files/{filename} download is not sufficient",),
    ),
    "report_generation": Capability(
        "report_generation",
        "renderer",
        ("routers/{resource}.py",),
        ("custom_routes", "required_behaviors"),
        ("generated_response_exists", "route_exists"),
    ),
    "custom_business_logic": Capability(
        "custom_business_logic",
        "bot",
        ("routers/{resource}.py",),
        ("custom_routes",),
        ("custom_route_exists", "runtime_import"),
    ),
    "websocket": Capability(
        "websocket",
        "mixed",
        ("websockets.py", "main.py"),
        ("required_behaviors",),
        ("websocket_route_exists", "broadcast_helper_exists"),
        ("missing_route_wiring",),
    ),
    "aggregation/top_n/pagination": Capability(
        "aggregation/top_n/pagination",
        "mixed",
        ("routers/{resource}.py",),
        ("required_behaviors", "custom_routes"),
        ("aggregate_route_exists", "pagination_params_exist"),
        ("missing_route", "missing_filter"),
    ),
}


def build_capability_plan(canonical_spec: dict[str, Any], artifact_contract: dict[str, Any]) -> dict[str, Any]:
    contract = normalize_artifact_contract(artifact_contract)
    detected: dict[str, dict[str, Any]] = {}

    if contract.get("required_tables") or _crud_like_routes(contract):
        _add_capability(detected, "crud_resource", "tables or CRUD routes requested")
    if contract.get("required_filters"):
        _add_capability(detected, "list_filter", "filter contract requested")
    if contract.get("required_relationships"):
        _add_capability(detected, "sqlmodel_relationship", "relationship contract requested")

    terms = _all_terms(canonical_spec, contract)
    behaviors = {
        identifier(item.get("behavior") or item.get("kind") or item.get("name"))
        for item in contract.get("required_behaviors") or []
        if isinstance(item, dict)
    }
    operations = {
        identifier(item.get("kind"))
        for item in canonical_spec.get("operations") or []
        if isinstance(item, dict)
    }
    custom_routes = [item for item in contract.get("custom_routes") or [] if isinstance(item, dict)]

    if {"jwt", "auth", "auth_basic", "auth_protected"} & (terms | behaviors | operations):
        _add_capability(detected, "jwt_auth_basic", "auth/JWT requested")
    if {"role", "roles", "roles_basic", "role_restricted"} & (terms | behaviors | operations):
        _add_capability(detected, "roles_basic", "roles requested")
    if {"admin", "admin_only", "admin_only_actions"} & (terms | behaviors | operations):
        _add_capability(detected, "admin_only_actions", "admin-only behavior requested")
    if "assignment" in terms or "assign" in terms or _has_assignment_relationship(contract):
        _add_capability(detected, "task_assignment", "assignment relationship requested")
    if {"file_upload", "upload", "download", "files"} & (terms | behaviors | operations):
        _add_capability(detected, "generic_file_upload_download", "generic file behavior requested")
    if _is_scoped_download(terms, custom_routes):
        _add_capability(detected, "scoped_user_download", "scoped user download requested")
    if {"report", "reports", "report_generation", "csv", "export"} & (terms | behaviors | operations):
        _add_capability(detected, "report_generation", "report/export behavior requested")
    if custom_routes and not _only_renderer_custom_routes(custom_routes, canonical_spec):
        _add_capability(detected, "custom_business_logic", "custom route requires focused code")
    if {"websocket", "websocket_notification"} & (terms | behaviors | operations):
        _add_capability(detected, "websocket", "websocket behavior requested")
    if {"pagination", "paginated", "top_n", "top_related", "parent_children_paginated", "aggregate_count_by_field"} & (
        terms | behaviors | operations
    ):
        _add_capability(detected, "aggregation/top_n/pagination", "aggregation or pagination requested")

    grouped = {"renderer": [], "bot": [], "mixed": []}
    for item in detected.values():
        grouped[item["owner"]].append(item)
    unsupported = [
        item
        for item in grouped["bot"]
        if item["capability_id"] in {"scoped_user_download", "report_generation", "custom_business_logic"}
    ]
    return {
        **grouped,
        "all": list(detected.values()),
        "unsupported_for_renderer": unsupported,
        "renderer_safe": not grouped["bot"],
    }


def capability_for_quest_kind(kind: str, quest: dict[str, Any] | None = None) -> str:
    if kind in {"table", "schema", "crud", "router", "wiring"}:
        return "crud_resource"
    if kind == "field":
        return "list_filter" if _quest_has_filter(quest or {}) else "crud_resource"
    if kind in {"relationship", "relationships"}:
        return "sqlmodel_relationship"
    if kind in {"auth"}:
        return "jwt_auth_basic"
    if kind in {"roles"}:
        return "roles_basic"
    if kind in {"file_helpers"}:
        return "generic_file_upload_download"
    if kind in {"custom_behavior"}:
        return "custom_business_logic"
    if kind == "behavior":
        behavior = identifier(((quest or {}).get("target_symbols") or [""])[0])
        if behavior in {"websocket", "websocket_notification"}:
            return "websocket"
        if behavior in {"pagination", "top_n", "top_related", "parent_children_paginated"}:
            return "aggregation/top_n/pagination"
        if "download" in behavior and "user" in behavior:
            return "scoped_user_download"
        if "upload" in behavior or "file" in behavior:
            return "generic_file_upload_download"
    if kind == "route":
        path = " ".join(str(symbol) for symbol in (quest or {}).get("target_symbols") or [])
        if any(term in path for term in ("download", "report", "export")):
            return "scoped_user_download" if "unfinished" in path or "current" in path else "report_generation"
    return "custom_business_logic"


def owner_for_capability(capability_id: str) -> CapabilityOwner:
    capability = CAPABILITY_REGISTRY.get(capability_id)
    return capability.owner if capability else "bot"


def renderer_supported_capability_ids() -> set[str]:
    return {
        capability.capability_id
        for capability in CAPABILITY_REGISTRY.values()
        if capability.owner == "renderer"
    }


def build_pipeline_audit(
    *,
    accepted: bool,
    capability_plan: dict[str, Any],
    quest_results: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    usage: Any,
    changed_files: list[str] | None = None,
    full_context_estimated_tokens: int = 0,
    selected_context_estimated_tokens: int = 0,
) -> dict[str, Any]:
    validation = validation or {}
    artifact = validation.get("artifact_validation") or {}
    runtime = validation.get("runtime_validation") or {}
    completed = [item for item in (quest_results or {}).get("completed") or [] if isinstance(item, dict)]
    failed = [item for item in (quest_results or {}).get("failed") or [] if isinstance(item, dict)]
    missing = artifact.get("missing_artifacts") or (quest_results or {}).get("missing_artifacts") or []
    regressions = artifact.get("regressions") or (quest_results or {}).get("regressions") or []
    total_required = len(artifact.get("covered_artifacts") or []) + len(missing) + len(regressions)
    if total_required:
        accuracy = max(0.0, len(artifact.get("covered_artifacts") or []) / total_required)
    else:
        accuracy = 1.0 if accepted and (validation.get("static_safe") or validation.get("accepted")) else 0.0
    selected_tokens = selected_context_estimated_tokens or _estimate_selected_tokens(quest_results)
    full_tokens = max(full_context_estimated_tokens, selected_tokens)
    saved = max(0, full_tokens - selected_tokens)
    renderer_files, bot_files, mixed_files = _generation_mix_counts(changed_files or [], capability_plan)
    total_files = max(1, renderer_files + bot_files + mixed_files)
    failed_quest = str(failed[0].get("id") or "") if failed else ""
    return {
        "accepted": bool(accepted),
        "accuracy_score": round(accuracy, 4),
        "capability_plan": {
            "renderer": [item.get("capability_id") for item in capability_plan.get("renderer") or []],
            "bot": [item.get("capability_id") for item in capability_plan.get("bot") or []],
            "mixed": [item.get("capability_id") for item in capability_plan.get("mixed") or []],
        },
        "quest_summary": {
            "total": len(completed) + len(failed),
            "passed": len(completed),
            "failed": len(failed),
            "repaired": _count_repaired_quests(completed),
        },
        "generation_mix": {
            "renderer_files": renderer_files,
            "bot_files": bot_files,
            "mixed_files": mixed_files,
            "renderer_ratio_pct": round(renderer_files * 100 / total_files, 2),
            "bot_ratio_pct": round(bot_files * 100 / total_files, 2),
        },
        "token_metrics": {
            "full_context_estimated_tokens": full_tokens,
            "selected_context_estimated_tokens": selected_tokens,
            "context_tokens_saved": saved,
            "context_tokens_saved_pct": round(saved * 100 / full_tokens, 2) if full_tokens else 0.0,
        },
        "failure_report": {
            "category": "" if accepted else validation.get("failure_category") or _failure_category(runtime, missing, regressions),
            "failed_quest": failed_quest,
            "missing_artifacts": missing,
            "next_repair": _next_repair(validation, failed_quest),
            "stopped_reason": "" if accepted else "no_progress" if failed else "validation_failed",
        },
    }


def _add_capability(target: dict[str, dict[str, Any]], capability_id: str, reason: str) -> None:
    capability = CAPABILITY_REGISTRY[capability_id]
    item = target.setdefault(capability_id, capability.as_dict() | {"reasons": []})
    item["reasons"].append(reason)


def _all_terms(canonical_spec: dict[str, Any], contract: dict[str, Any]) -> set[str]:
    text_parts = [str(canonical_spec.get("request_terms") or "")]
    for key in ("prompt", "summary", "description"):
        text_parts.append(str(canonical_spec.get(key) or contract.get(key) or ""))
    for values in [canonical_spec.get("operations") or [], contract.get("required_behaviors") or [], contract.get("custom_routes") or []]:
        text_parts.extend(str(item) for item in values if isinstance(item, dict))
    return {identifier(part) for text in text_parts for part in str(text).replace("-", "_").split()}


def _crud_like_routes(contract: dict[str, Any]) -> bool:
    return any(
        isinstance(route, dict)
        and str(route.get("method") or "").upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        for route in contract.get("required_routes") or []
    )


def _has_assignment_relationship(contract: dict[str, Any]) -> bool:
    for item in contract.get("required_relationships") or []:
        if not isinstance(item, dict):
            continue
        values = {plural(item.get("from_table")), plural(item.get("to_table")), identifier(item.get("field"))}
        if "tasks" in values and ("users" in values or "user_id" in values or "assignee_id" in values):
            return True
    return False


def _is_scoped_download(terms: set[str], custom_routes: list[dict[str, Any]]) -> bool:
    if "download" not in terms and not any("download" in str(route.get("path") or "") for route in custom_routes):
        return False
    return bool({"current", "me", "my", "user", "unfinished", "scoped"} & terms) or any(
        any(term in str(route).lower() for term in ("current user", "unfinished", "my "))
        for route in custom_routes
    )


def _only_renderer_custom_routes(
    custom_routes: list[dict[str, Any]],
    canonical_spec: dict[str, Any],
) -> bool:
    renderer_kinds = {
        "top_related",
        "parent_children_paginated",
        "aggregate_count_by_field",
        "file_download",
    }
    return all(
        identifier(item.get("kind")) in renderer_kinds
        or (
            identifier(item.get("kind")) == "explicit"
            and "download" in str(item.get("path") or "").lower()
            and str(item.get("method") or "GET").upper() == "GET"
        )
        or _is_standard_resource_route(item, canonical_spec)
        for item in custom_routes
    )


def _is_standard_resource_route(item: dict[str, Any], canonical_spec: dict[str, Any]) -> bool:
    if identifier(item.get("kind")) != "explicit":
        return False
    method = str(item.get("method") or "").upper()
    path_parts = [part for part in str(item.get("path") or "").strip("/").split("/") if part]
    if not path_parts:
        return False
    resource_bases = {
        plural(resource.get("table") or resource.get("name") or "")
        for resource in canonical_spec.get("resources") or []
        if isinstance(resource, dict)
    }
    if path_parts[0] not in resource_bases:
        return False
    if len(path_parts) == 1:
        return method in {"GET", "POST"}
    return len(path_parts) == 2 and path_parts[1].startswith("{") and method in {
        "GET",
        "PUT",
        "PATCH",
        "DELETE",
    }


def _quest_has_filter(quest: dict[str, Any]) -> bool:
    contract = quest.get("artifact_contract") if isinstance(quest.get("artifact_contract"), dict) else {}
    return bool(contract.get("required_filters"))


def _estimate_selected_tokens(quest_results: dict[str, Any] | None) -> int:
    budget = (quest_results or {}).get("context_budget") or {}
    chars = int(budget.get("selected_context_chars", 0) or 0)
    return chars // 4


def _generation_mix_counts(files: list[str], capability_plan: dict[str, Any]) -> tuple[int, int, int]:
    count = len(files)
    if capability_plan.get("bot") and not capability_plan.get("renderer"):
        return 0, count, 0
    if capability_plan.get("bot"):
        return 0, 0, count
    if capability_plan.get("mixed"):
        return 0, 0, count
    return count, 0, 0


def _count_repaired_quests(completed: list[dict[str, Any]]) -> int:
    return sum(1 for item in completed if item.get("repaired"))


def _failure_category(runtime: dict[str, Any], missing: list[str], regressions: list[str]) -> str:
    if regressions:
        return "regression"
    if runtime and not runtime.get("passed", True):
        return "runtime_validation"
    if missing:
        return "missing_artifact"
    return "validation_failed"


def _next_repair(validation: dict[str, Any], failed_quest: str) -> str:
    repair_plan = validation.get("repair_plan") or {}
    affected = repair_plan.get("affected_files") if isinstance(repair_plan, dict) else None
    if failed_quest:
        return f"repair quest {failed_quest}"
    if affected:
        return "focused repair for " + ", ".join(str(item) for item in affected[:4])
    category = validation.get("failure_category") or ""
    return f"deterministic repair for {category}" if category else ""
