from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.model_pipeline.profiles import FASTAPI_PROFILE

CONTRACT_KEYS = (
    "required_routes",
    "required_tables",
    "required_fields",
    "required_filters",
    "required_relationships",
    "required_behaviors",
    "removed_artifacts",
)


@dataclass
class ArtifactValidation:
    passed: bool
    contract: dict[str, Any]
    missing_artifacts: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    covered_artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detected_artifacts: dict[str, Any] = field(default_factory=dict)
    checked_contracts: int = 0
    requirement_findings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "contract": self.contract,
            "missing_artifacts": self.missing_artifacts,
            "regressions": self.regressions,
            "covered_artifacts": self.covered_artifacts,
            "warnings": self.warnings,
            "detected_artifacts": self.detected_artifacts,
            "checked_contracts": self.checked_contracts,
            "requirement_findings": self.requirement_findings,
        }


def identifier(value: Any) -> str:
    raw = re.sub(r"(?<!^)(?=[A-Z])", "_", str(value or ""))
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", raw.lower()).strip("_")
    return raw


def singular(value: Any) -> str:
    raw = identifier(value)
    if raw == "statuses":
        return "status"
    if raw.endswith("ies") and len(raw) > 3:
        return raw[:-3] + "y"
    if raw.endswith(("sses", "xes", "zes", "ches", "shes")) and len(raw) > 4:
        return raw[:-2]
    if raw.endswith("s") and not raw.endswith("ss") and len(raw) > 3:
        return raw[:-1]
    return raw


def camel(value: Any) -> str:
    base = singular(value)
    return "".join(part[:1].upper() + part[1:] for part in base.split("_") if part) or "Item"


def canonical_path(path: Any) -> str:
    value = str(path or "").strip().rstrip(".,;")
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def route_shape(path: Any) -> str:
    return re.sub(r"\{[^}/]+\}", "{}", canonical_path(path))


def _add_unique(target: list[dict[str, Any]], item: dict[str, Any], keys: tuple[str, ...]) -> None:
    normalized = {
        key: value
        for key, value in item.items()
        if value is not None and value != ""
    }
    if not normalized:
        return
    marker = tuple(str(normalized.get(key, "")).lower() for key in keys)
    for existing in target:
        if tuple(str(existing.get(key, "")).lower() for key in keys) == marker:
            return
    target.append(normalized)


def empty_contract(
    *,
    source: str = "",
    prompt: str = "",
    requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "prompt": prompt,
        "requirements": requirements or [],
        "required_routes": [],
        "required_tables": [],
        "required_fields": [],
        "required_filters": [],
        "required_relationships": [],
        "required_behaviors": [],
        "removed_artifacts": [],
    }


def normalize_requirement_contract(
    contract: dict[str, Any] | None = None,
    *,
    prompt: str = "",
    requirements: list[dict[str, Any]] | None = None,
    source: str = "",
    prompt_contract: dict[str, Any] | None = None,
    edit_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = empty_contract(
        source=source or str((contract or {}).get("source") or ""),
        prompt=prompt or str((contract or {}).get("prompt") or ""),
        requirements=requirements or list((contract or {}).get("requirements") or []),
    )
    if contract:
        for key, value in contract.items():
            if key not in CONTRACT_KEYS and key not in base:
                base[key] = value
        if not base["requirements"]:
            base["requirements"] = list(contract.get("requirements") or [])
        for key in CONTRACT_KEYS:
            value = contract.get(key)
            if isinstance(value, list):
                base[key].extend(item for item in value if isinstance(item, dict))

    if isinstance(prompt_contract, dict):
        base["prompt_contract"] = prompt_contract
    if edit_plan and not base["prompt"]:
        base["prompt"] = str(edit_plan.get("prompt") or edit_plan.get("conversation_state") or "")

    for key in CONTRACT_KEYS:
        base[key] = [_normalize_artifact_item(key, item) for item in base[key] if isinstance(item, dict)]
    return base


def _normalize_artifact_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["requirement_id"] = str(out.get("requirement_id") or out.get("id") or "R?")
    if kind == "required_routes":
        out["method"] = str(out.get("method") or "*").upper()
        out["path"] = canonical_path(out.get("path"))
    elif kind in {"required_tables"}:
        out["table"] = camel(out.get("table") or out.get("name"))
    elif kind in {"required_fields", "required_filters"}:
        out["table"] = camel(out.get("table") or out.get("model") or out.get("class") or "")
        out["field"] = identifier(out.get("field") or out.get("name") or "")
    elif kind == "required_relationships":
        out["from_table"] = camel(
            out.get("from_table")
            or out.get("from_table_name")
            or out.get("table")
            or out.get("table_name")
            or ""
        )
        out["to_table"] = camel(
            out.get("to_table")
            or out.get("to_table_name")
            or out.get("target_table")
            or out.get("target_table_name")
            or out.get("related_table")
            or out.get("related_table_name")
            or out.get("references_table")
            or out.get("references_table_name")
            or out.get("to")
            or ""
        )
        columns = out.get("columns")
        first_column = columns[0] if isinstance(columns, list) and columns else ""
        out["field"] = identifier(
            out.get("field")
            or out.get("field_name")
            or out.get("column")
            or out.get("column_name")
            or out.get("from_column")
            or out.get("name")
            or first_column
            or ""
        )
    elif kind == "required_behaviors":
        out["behavior"] = identifier(out.get("behavior") or out.get("name") or out.get("kind") or "")
        if out.get("table"):
            out["table"] = camel(out.get("table"))
    elif kind == "removed_artifacts":
        out["kind"] = identifier(out.get("kind") or "artifact")
        if out.get("table"):
            out["table"] = camel(out.get("table"))
        if out.get("field"):
            out["field"] = identifier(out.get("field"))
        if out.get("path"):
            out["path"] = Path(str(out["path"]).replace("\\", "/")).as_posix().strip("/")
    return out


def _table_map(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tables = (index.get("database_schema") or {}).get("tables") or []
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        name = str(table.get("name") or "")
        db_name = str(table.get("db_table_name") or "")
        for key in {identifier(name), singular(name), singular(camel(name)), identifier(db_name), singular(db_name)}:
            if key:
                result[key] = table
    return result


def _detected_artifacts(index: dict[str, Any]) -> dict[str, Any]:
    tables = (index.get("database_schema") or {}).get("tables") or []
    classes = index.get("class_summaries") or []
    files = index.get("file_index") or []
    return {
        "tables": [
            {
                "name": table.get("name"),
                "db_table_name": table.get("db_table_name"),
                "file": table.get("file"),
                "fields": [field.get("name") for field in table.get("fields") or []],
                "kind": table.get("kind"),
            }
            for table in tables[:80]
        ],
        "classes": [
            {
                "name": item.get("name"),
                "file": item.get("file"),
                "bases": item.get("bases") or [],
                "db_table_name": item.get("db_table_name"),
                "is_sqlmodel_table": item.get("is_sqlmodel_table"),
                "is_sqlalchemy_table": item.get("is_sqlalchemy_table"),
                "is_pydantic_model": item.get("is_pydantic_model"),
            }
            for item in classes[:120]
        ],
        "files_scanned": [file.get("path") for file in files[:160]],
        "stats": index.get("stats") or {},
    }


def _route_matches(index: dict[str, Any], method: str, path: str) -> dict[str, Any] | None:
    target_method = method.upper()
    target_path = canonical_path(path)
    target_shape = route_shape(target_path)
    for route in index.get("api_routes") or []:
        route_method = str(route.get("method") or "").upper()
        route_path = canonical_path(route.get("path"))
        method_matches = target_method in {"", "*", "ANY"} or route_method == target_method
        if method_matches and (route_path == target_path or route_shape(route_path) == target_shape or _route_name_equivalent(route_path, target_path)):
            return route
    return None


def _route_name_equivalent(left: str, right: str) -> bool:
    left_parts = [part for part in canonical_path(left).strip("/").split("/") if part]
    right_parts = [part for part in canonical_path(right).strip("/").split("/") if part]
    if len(left_parts) != len(right_parts):
        return False
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part.startswith("{") or right_part.startswith("{"):
            if left_part != right_part:
                return False
        elif left_part.replace("_", "") != right_part.replace("_", ""):
            return False
    return True


def _wired_router_files(index: dict[str, Any]) -> set[str]:
    files = {FASTAPI_PROFILE.entrypoint_file}
    wiring = [
        item
        for item in (index.get("router_wiring") or [])
        if isinstance(item, dict)
    ]
    changed = True
    while changed:
        changed = False
        for item in wiring:
            if str(item.get("file") or "") not in files:
                continue
            file_path = str(item.get("router_file") or "")
            if file_path and file_path not in files:
                files.add(file_path)
                changed = True
    return files


def _field_exists(table: dict[str, Any] | None, field_name: str) -> bool:
    if not table:
        return False
    target = identifier(field_name)
    return any(identifier(field.get("name")) == target for field in table.get("fields") or [])


def _foreign_key_for(table: dict[str, Any] | None, field_name: str) -> str:
    if not table:
        return ""
    target = identifier(field_name)
    for field_item in table.get("fields") or []:
        if identifier(field_item.get("name")) == target:
            return str(field_item.get("foreign_key") or "")
    return ""


def _field_info(table: dict[str, Any] | None, field_name: str) -> dict[str, Any] | None:
    if not table:
        return None
    target = identifier(field_name)
    for field_item in table.get("fields") or []:
        if identifier(field_item.get("name")) == target:
            return field_item
    return None


def _primary_key_info(table: dict[str, Any] | None) -> dict[str, Any] | None:
    if not table:
        return None
    for field_item in table.get("fields") or []:
        if field_item.get("primary_key"):
            return field_item
    return _field_info(table, "id")


def _type_family(raw_type: Any) -> str:
    text = str(raw_type or "").lower().replace("none", "")
    if any(term in text for term in ("int", "integer", "bigint", "smallint")):
        return "int"
    if any(term in text for term in ("float", "real", "double", "decimal", "numeric")):
        return "float"
    if any(term in text for term in ("str", "text", "varchar", "char", "string")):
        return "str"
    if "bool" in text:
        return "bool"
    if "date" in text or "time" in text:
        return "datetime"
    return identifier(text)


def _semantic_relationship_issues(index: dict[str, Any]) -> list[str]:
    tables = _table_map(index)
    issues: list[str] = []
    seen: set[str] = set()
    for table in (index.get("database_schema") or {}).get("tables") or []:
        table_name = str(table.get("name") or "")
        table_key = singular(table_name)
        for field_item in table.get("fields") or []:
            field_name = identifier(field_item.get("name"))
            if not field_name.endswith("_id") or field_name == "id":
                continue
            target_key = singular(field_name.removesuffix("_id"))
            target_table = tables.get(target_key)
            if not target_table or singular(target_table.get("name")) == table_key:
                continue
            target_name = str(target_table.get("name") or target_key)
            fk = str(field_item.get("foreign_key") or "")
            expected_target = singular(target_name)
            if not fk or expected_target not in {singular(fk.split(".", 1)[0]), singular(fk)}:
                message = (
                    f"semantic relationship mismatch: {camel(table_name)}.{field_name} "
                    f"should foreign key {camel(target_name)}.id"
                )
                if message not in seen:
                    issues.append(message)
                    seen.add(message)
            source_family = _type_family(field_item.get("type"))
            target_pk = _primary_key_info(target_table)
            target_family = _type_family((target_pk or {}).get("type"))
            if source_family and target_family and source_family != target_family:
                message = (
                    f"semantic relationship type mismatch: {camel(table_name)}.{field_name} "
                    f"is {field_item.get('type')}, but {camel(target_name)}.id is {(target_pk or {}).get('type')}"
                )
                if message not in seen:
                    issues.append(message)
                    seen.add(message)
    return issues


def _filter_exists(index: dict[str, Any], table: str, field_name: str) -> bool:
    table_key = singular(table)
    field_key = identifier(field_name)
    for item in index.get("query_filters") or []:
        if singular(item.get("table")) == table_key and identifier(item.get("field")) == field_key:
            return True
    for route in index.get("api_routes") or []:
        if "{" in str(route.get("path") or ""):
            continue
        if table_key not in _route_table_candidates(route):
            continue
        for param in route.get("parameters") or []:
            if param.get("in") == "query" and identifier(param.get("name")) == field_key:
                return True
    return False


def _route_table_candidates(route: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    path = canonical_path(route.get("path"))
    for part in path.strip("/").split("/"):
        if part and not part.startswith("{"):
            candidates.add(singular(part))
            break
    file_path = str(route.get("file") or "")
    if file_path:
        candidates.add(singular(Path(file_path).stem))
    response_model = str(route.get("response_model") or "")
    if response_model:
        tail = response_model.replace("list[", "").replace("List[", "").replace("]", "")
        tail = tail.rsplit(".", 1)[-1]
        tail = re.sub(r"(Read|Out|Response|Schema|DTO|Dto)$", "", tail)
        candidates.add(singular(tail))
    return {item for item in candidates if item}


def _route_function(
    index: dict[str, Any],
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    method = str(item.get("method") or "*")
    path = str(item.get("path") or "")
    route = _route_matches(index, method, path) if path else None
    if route is None:
        return None, None
    function = next(
        (
            candidate
            for candidate in index.get("function_summaries") or []
            if str(candidate.get("file") or "") == str(route.get("file") or "")
            and str(candidate.get("name") or "") == str(route.get("function") or "")
        ),
        None,
    )
    return route, function


def _behavior_exists(
    index: dict[str, Any],
    behavior: str,
    table: str = "",
    item: dict[str, Any] | None = None,
) -> bool:
    behavior = identifier(behavior)
    item = item or {}
    if not behavior:
        return False
    tables = (index.get("database_schema") or {}).get("tables") or []
    routes = index.get("api_routes") or []
    route_paths = {canonical_path(route.get("path")) for route in routes}
    functions = index.get("function_summaries") or []
    symbols = {
        identifier(symbol)
        for fn in functions
        for symbol in (fn.get("body_symbols") or [])
    }
    project_terms = set(symbols)
    project_terms.update(identifier(fn.get("name")) for fn in functions)
    project_terms.update(identifier(route.get("function")) for route in routes)
    for path in route_paths:
        project_terms.update(identifier(part.strip("{}")) for part in path.split("/") if part.strip("{}"))
    for table_item in tables:
        project_terms.add(singular(table_item.get("name")))
        for field_item in table_item.get("fields") or []:
            project_terms.add(identifier(field_item.get("name")))

    if behavior == "file_exists":
        target_path = str(item.get("path") or "").replace("\\", "/")
        return any(
            str(file_item.get("path") or "").replace("\\", "/") == target_path
            for file_item in index.get("file_index") or []
        )
    if behavior == "route_auth_protected":
        route, _ = _route_function(index, item)
        return route is not None and _route_has_auth_dependency(route)

    if behavior in {"jwt", "jwt_auth", "jwt_authentication", "j_w_t_auth", "j_w_t_authentication"}:
        has_token_route = any("token" in canonical_path(route.get("path")) for route in routes)
        has_auth_symbols = any(
            term in symbol
            for symbol in project_terms
            for term in ("jwt", "access_token", "create_access_token", "get_current_user")
        )
        return has_token_route and has_auth_symbols

    if behavior == "file_download":
        response_terms = {
            "response",
            "streaming_response",
            "file_response",
            "plain_text_response",
        }
        if item.get("path"):
            candidates = [_route_function(index, item)]
        else:
            candidates = [
                (route, function)
                for route in routes
                if str(route.get("method") or "").upper() == str(item.get("method") or "GET").upper()
                for function in functions
                if str(function.get("file") or "") == str(route.get("file") or "")
                and str(function.get("name") or "") == str(route.get("function") or "")
                and any(
                    term in f"{canonical_path(route.get('path'))} {identifier(route.get('function'))}"
                    for term in ("download", "file")
                )
            ]
        for route, function in candidates:
            if route is None or function is None:
                continue
            function_symbols = {identifier(symbol) for symbol in function.get("body_symbols") or []}
            if not function_symbols & response_terms and not any(
                term in symbol
                for symbol in function_symbols
                for term in response_terms
            ):
                continue
            if str(item.get("format") or "").lower() == "csv" and not any(
                term in symbol
                for symbol in function_symbols
                for term in ("csv", "writer", "string_io", "text_csv")
            ):
                continue
            expected_table = singular(item.get("table"))
            if expected_table and not any(
                expected_table == singular(symbol)
                or expected_table in singular(symbol)
                for symbol in function_symbols
            ):
                continue
            return True
        return False
    if behavior == "role_restricted":
        route, function = _route_function(index, item)
        if route is None or function is None or not _route_has_auth_dependency(route):
            return False
        symbols = {identifier(symbol) for symbol in function.get("body_symbols") or []}
        has_subject = any(term in symbol for symbol in symbols for term in ("current_user", "role"))
        has_denial = any(
            term in symbol
            for symbol in symbols
            for term in ("403", "forbidden", "permission", "require_role")
        )
        return has_subject and has_denial
    if behavior == "current_user_scoped":
        route, function = _route_function(index, item)
        if route is None or function is None or not _route_has_auth_dependency(route):
            return False
        symbols = {identifier(symbol) for symbol in function.get("body_symbols") or []}
        has_subject = any("current_user" in symbol for symbol in symbols)
        query_filters = function.get("query_filters") or []
        has_scope = any(
            "current_user" in identifier(filter_item.get("value"))
            or identifier(filter_item.get("field")) in {"user_id", "student_id", "assignee_id", "owner_id"}
            for filter_item in query_filters
        )
        return has_subject and has_scope

    if behavior in {"ownership", "owner_scoped", "user_scoped"}:
        target = singular(table)
        has_owner = any(
            (not target or singular(table_item.get("name")) == target)
            and _field_exists(table_item, "owner_id")
            for table_item in tables
        )
        return has_owner and ("get_current_user" in symbols or any("current_user" in symbol for symbol in symbols))
    if behavior in {"auth_protected", "protected_routes", "protected_endpoints"}:
        target = singular(table)
        protected_routes = [
            route
            for route in routes
            if not target or target in _route_table_candidates(route)
            if not any(
                term in canonical_path(route.get("path"))
                for term in ("/auth/token", "/login", "/register")
            )
            if not (
                str(route.get("method") or "").upper() == "POST"
                and canonical_path(route.get("path")) in {"/users", "/accounts"}
            )
        ]
        if not protected_routes:
            return False
        return all(_route_has_auth_dependency(route) for route in protected_routes)
    if behavior == "websocket":
        return any(str(route.get("method") or "").upper() == "WEBSOCKET" for route in routes) or any(
            "websocket" in symbol for symbol in symbols
        )
    if behavior in {"websocket_notification", "websocket_notifications"}:
        has_socket = any(str(route.get("method") or "").upper() == "WEBSOCKET" for route in routes)
        notification_terms = {"broadcast_notification", "broadcast", "active_connections", "send_json"}
        return has_socket and bool(notification_terms & project_terms)
    if behavior in {"pagination", "paginated"}:
        pagination_terms = {"page", "page_size", "limit", "offset"}
        return bool(pagination_terms & project_terms)
    if behavior in {"top_n", "top", "ranking"}:
        top_terms = {"top", "limit", "order_by", "desc", "group_by", "count"}
        return bool(top_terms & project_terms) or any("top" in canonical_path(route.get("path")) for route in routes)
    if behavior in {"aggregate", "aggregation", "summary", "group_by"}:
        aggregate_terms = {"group_by", "count", "summary", "analytics"}
        return bool(aggregate_terms & project_terms)
    if behavior in {"file_upload", "upload_file", "file"}:
        return any("upload_file" in symbol or "uploadfile" in symbol or symbol == "file" for symbol in symbols) or any(
            "files" in _route_table_candidates(route) for route in routes
        )

    behavior_parts = {part for part in behavior.split("_") if len(part) > 2}
    if behavior in project_terms:
        return True
    if any(behavior in term or term in behavior for term in project_terms if len(term) > 2):
        return True
    return bool(behavior_parts) and behavior_parts <= project_terms


def _route_has_auth_dependency(route: dict[str, Any]) -> bool:
    for param in route.get("parameters") or []:
        if param.get("in") != "dependency":
            continue
        haystack = " ".join(
            str(param.get(key) or "")
            for key in ("name", "type", "default")
        ).lower()
        if any(term in haystack for term in ("current_user", "get_current_user", "jwt", "token", "auth")):
            return True
    return False


def _artifact_label(kind: str, item: dict[str, Any]) -> str:
    req = str(item.get("requirement_id") or "R?")
    if kind == "route":
        return f"{req} missing route {item.get('method')} {item.get('path')}"
    if kind == "route_wiring":
        return (
            f"{req} route {item.get('method')} {item.get('path')} "
            f"is not wired in {FASTAPI_PROFILE.route_wiring_label}"
        )
    if kind == "table":
        return f"{req} missing table {item.get('table')}"
    if kind == "field":
        return f"{req} missing field {item.get('table')}.{item.get('field')}"
    if kind == "filter":
        return f"{req} missing filter {item.get('table')}.{item.get('field')}"
    if kind == "relationship":
        return f"{req} missing relationship {item.get('from_table')}.{item.get('field')} -> {item.get('to_table')}"
    if kind == "behavior":
        suffix = f" for {item.get('table')}" if item.get("table") else ""
        return f"{req} missing behavior {item.get('behavior')}{suffix}"
    if kind == "removed":
        return f"{req} artifact was not removed: {item}"
    if kind == "local_import":
        return f"unresolved local import in {item.get('file')}: {item.get('error')}"
    if kind == "unverifiable":
        return f"{req} has no model-supplied artifact checks"
    return f"{req} missing artifact {item}"


def _validate_one(index: dict[str, Any], contract: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    missing: list[str] = []
    covered: list[str] = []
    warnings: list[str] = []
    tables = _table_map(index)
    wired = _wired_router_files(index)

    for item in contract.get("required_tables") or []:
        table = tables.get(singular(item.get("table")))
        if not table:
            missing.append(_artifact_label("table", item))
        else:
            covered.append(f"{item.get('requirement_id')} table {item.get('table')}")

    for item in contract.get("required_fields") or []:
        table = tables.get(singular(item.get("table")))
        if not _field_exists(table, str(item.get("field") or "")):
            missing.append(_artifact_label("field", item))
        else:
            covered.append(f"{item.get('requirement_id')} field {item.get('table')}.{item.get('field')}")

    for item in contract.get("required_routes") or []:
        route = _route_matches(index, str(item.get("method") or ""), str(item.get("path") or ""))
        if not route:
            missing.append(_artifact_label("route", item))
            continue
        file_path = str(route.get("file") or "")
        if file_path.startswith("routers/") and file_path not in wired:
            missing.append(_artifact_label("route_wiring", item))
        else:
            covered.append(f"{item.get('requirement_id')} route {item.get('method')} {item.get('path')}")

    for item in contract.get("required_filters") or []:
        table = tables.get(singular(item.get("table")))
        if not _field_exists(table, str(item.get("field") or "")) or not _filter_exists(
            index, str(item.get("table") or ""), str(item.get("field") or "")
        ):
            missing.append(_artifact_label("filter", item))
        else:
            covered.append(f"{item.get('requirement_id')} filter {item.get('table')}.{item.get('field')}")

    for item in contract.get("required_relationships") or []:
        table = tables.get(singular(item.get("from_table")))
        fk = _foreign_key_for(table, str(item.get("field") or ""))
        target = singular(item.get("to_table"))
        if not fk or target not in {singular(fk.split(".", 1)[0]), singular(fk)}:
            missing.append(_artifact_label("relationship", item))
        else:
            covered.append(
                f"{item.get('requirement_id')} relationship {item.get('from_table')}.{item.get('field')}"
            )

    for item in contract.get("required_behaviors") or []:
        if not _behavior_exists(
            index,
            str(item.get("behavior") or ""),
            table=str(item.get("table") or ""),
            item=item,
        ):
            missing.append(_artifact_label("behavior", item))
        else:
            covered.append(f"{item.get('requirement_id')} behavior {item.get('behavior')}")

    for item in contract.get("removed_artifacts") or []:
        kind = identifier(item.get("kind") or "")
        if kind == "file":
            exists = any(str(file_item.get("path") or "") == item.get("path") for file_item in index.get("file_index") or [])
        elif kind == "field":
            exists = _field_exists(tables.get(singular(item.get("table"))), str(item.get("field") or ""))
        elif kind == "table":
            exists = singular(item.get("table")) in tables
        elif kind == "route":
            exists = _route_matches(index, str(item.get("method") or ""), str(item.get("path") or "")) is not None
        else:
            exists = False
            warnings.append(f"Unknown removed artifact kind: {kind}")
        if exists:
            missing.append(_artifact_label("removed", item))
        else:
            covered.append(f"{item.get('requirement_id')} removed {kind}")

    for file_info in index.get("file_index") or []:
        for error in file_info.get("local_import_errors") or []:
            missing.append(_artifact_label("local_import", {"file": file_info.get("path"), "error": error}))

    has_requirements = bool(contract.get("requirements"))
    has_artifacts = any(contract.get(key) for key in CONTRACT_KEYS)
    if has_requirements and not has_artifacts:
        for req in contract.get("requirements") or []:
            if req.get("critical", True):
                missing.append(_artifact_label("unverifiable", {"requirement_id": req.get("id") or "R?"}))

    return missing, covered, warnings


def _accepted_previous_contract(contract: dict[str, Any]) -> bool:
    if not isinstance(contract, dict):
        return False
    return contract.get("accepted") is True


def build_preservation_contract(index: dict[str, Any]) -> dict[str, Any]:
    contract = empty_contract(source="project_baseline")
    contract["accepted"] = True
    for route in index.get("api_routes") or []:
        method = str(route.get("method") or "").upper()
        path = canonical_path(route.get("path"))
        if not method or not path:
            continue
        contract["required_routes"].append(
            {
                "requirement_id": "baseline",
                "method": method,
                "path": path,
            }
        )
        if _route_has_auth_dependency(route):
            contract["required_behaviors"].append(
                {
                    "requirement_id": "baseline",
                    "behavior": "route_auth_protected",
                    "method": method,
                    "path": path,
                }
            )
    for table in (index.get("database_schema") or {}).get("tables") or []:
        table_name = str(table.get("name") or "")
        if not table_name:
            continue
        contract["required_tables"].append(
            {"requirement_id": "baseline", "table": table_name}
        )
        for field_item in table.get("fields") or []:
            field_name = str(field_item.get("name") or "")
            if field_name:
                contract["required_fields"].append(
                    {
                        "requirement_id": "baseline",
                        "table": table_name,
                        "field": field_name,
                    }
                )
            foreign_key = str(field_item.get("foreign_key") or "")
            if field_name and foreign_key:
                contract["required_relationships"].append(
                    {
                        "requirement_id": "baseline",
                        "from_table": table_name,
                        "field": field_name,
                        "to_table": foreign_key.split(".", 1)[0],
                    }
                )
    for filter_item in index.get("query_filters") or []:
        table_name = str(filter_item.get("table") or "")
        field_name = str(filter_item.get("field") or "")
        if table_name and field_name:
            contract["required_filters"].append(
                {
                    "requirement_id": "baseline",
                    "table": table_name,
                    "field": field_name,
                }
            )
    for file_item in index.get("file_index") or []:
        path = str(file_item.get("path") or "")
        if path.endswith(".py"):
            contract["required_behaviors"].append(
                {
                    "requirement_id": "baseline",
                    "behavior": "file_exists",
                    "path": path,
                }
            )
    return normalize_requirement_contract(contract)


def _without_explicit_removals(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    allowed = current.get("removed_artifacts") or []
    if not allowed:
        return previous
    result = {key: list(value) if isinstance(value, list) else value for key, value in previous.items()}
    for removal in allowed:
        kind = identifier(removal.get("kind"))
        table = singular(removal.get("table"))
        field_name = identifier(removal.get("field"))
        method = str(removal.get("method") or "*").upper()
        path = canonical_path(removal.get("path")) if removal.get("path") else ""
        if kind == "route":
            result["required_routes"] = [
                item
                for item in result.get("required_routes") or []
                if not (
                    route_shape(item.get("path")) == route_shape(path)
                    and method in {"*", str(item.get("method") or "").upper()}
                )
            ]
            result["required_behaviors"] = [
                item
                for item in result.get("required_behaviors") or []
                if not (
                    item.get("behavior") == "route_auth_protected"
                    and route_shape(item.get("path")) == route_shape(path)
                )
            ]
        elif kind == "field":
            result["required_fields"] = [
                item
                for item in result.get("required_fields") or []
                if not (
                    singular(item.get("table")) == table
                    and identifier(item.get("field")) == field_name
                )
            ]
            result["required_filters"] = [
                item
                for item in result.get("required_filters") or []
                if not (
                    singular(item.get("table")) == table
                    and identifier(item.get("field")) == field_name
                )
            ]
        elif kind == "table":
            result["required_tables"] = [
                item
                for item in result.get("required_tables") or []
                if singular(item.get("table")) != table
            ]
            for key in ("required_fields", "required_filters"):
                result[key] = [
                    item
                    for item in result.get(key) or []
                    if singular(item.get("table")) != table
                ]
        elif kind == "file":
            normalized_path = str(removal.get("path") or "").replace("\\", "/")
            result["required_behaviors"] = [
                item
                for item in result.get("required_behaviors") or []
                if not (
                    item.get("behavior") == "file_exists"
                    and str(item.get("path") or "").replace("\\", "/") == normalized_path
                )
            ]
    return result


def _requirement_findings(
    contract: dict[str, Any],
    missing: list[str],
    regressions: list[str],
    covered: list[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in covered[:200]:
        findings.append({"state": "satisfied", "evidence": item, "confidence": 1.0})
    for item in missing[:100]:
        findings.append({
            "state": "partial",
            "evidence": [],
            "missing": item,
            "confidence": 0.0,
            "repair_priority": "high",
        })
    for item in regressions[:100]:
        findings.append({
            "state": "contradicted",
            "evidence": [],
            "missing": item,
            "confidence": 0.0,
            "repair_priority": "critical",
        })
    return findings


def validate_artifacts(
    index: dict[str, Any],
    contract: dict[str, Any],
    *,
    previous_contracts: list[dict[str, Any]] | None = None,
) -> ArtifactValidation:
    normalized = normalize_requirement_contract(contract)
    missing, covered, warnings = _validate_one(index, normalized)
    missing.extend(_semantic_relationship_issues(index))
    regressions: list[str] = []
    checked = 1
    for previous in previous_contracts or []:
        if not _accepted_previous_contract(previous):
            continue
        previous_normalized = _without_explicit_removals(
            normalize_requirement_contract(previous),
            normalized,
        )
        previous_missing, previous_covered, previous_warnings = _validate_one(index, previous_normalized)
        checked += 1
        covered.extend(previous_covered)
        warnings.extend(previous_warnings)
        regressions.extend(previous_missing)
    covered_unique = list(dict.fromkeys(covered))[:200]
    missing_unique = list(dict.fromkeys(missing))[:100]
    regression_unique = list(dict.fromkeys(regressions))[:100]
    return ArtifactValidation(
        passed=not missing_unique and not regression_unique,
        contract=normalized,
        missing_artifacts=missing_unique,
        regressions=regression_unique,
        covered_artifacts=covered_unique,
        warnings=list(dict.fromkeys(warnings))[:50],
        detected_artifacts=_detected_artifacts(index),
        checked_contracts=checked,
        requirement_findings=_requirement_findings(
            normalized,
            missing_unique,
            regression_unique,
            covered_unique,
        ),
    )


def artifact_file_hints(index: dict[str, Any], contracts: list[dict[str, Any]] | dict[str, Any]) -> list[str]:
    if isinstance(contracts, dict):
        items = [contracts]
    else:
        items = [item for item in contracts if isinstance(item, dict)]
    table_files: dict[str, str] = {}
    for table in (index.get("database_schema") or {}).get("tables") or []:
        if table.get("file"):
            table_files[singular(table.get("name"))] = str(table["file"])
    route_files: dict[tuple[str, str], str] = {}
    for route in index.get("api_routes") or []:
        route_files[(str(route.get("method") or "").upper(), canonical_path(route.get("path")))] = str(route.get("file") or "")

    hints: list[str] = []

    def add(path: str) -> None:
        if path and path not in hints:
            hints.append(path)

    for raw_contract in items:
        contract = normalize_requirement_contract(raw_contract)
        for key in ("required_tables", "required_fields", "required_filters"):
            for item in contract.get(key) or []:
                add(table_files.get(singular(item.get("table")), ""))
        for item in contract.get("required_relationships") or []:
            add(table_files.get(singular(item.get("from_table")), ""))
            add(table_files.get(singular(item.get("to_table")), ""))
        for item in contract.get("required_routes") or []:
            add(route_files.get((str(item.get("method") or "").upper(), canonical_path(item.get("path"))), ""))
            add(FASTAPI_PROFILE.entrypoint_file)
        if contract.get("required_routes") or contract.get("required_behaviors"):
            add(FASTAPI_PROFILE.entrypoint_file)
        for item in contract.get("removed_artifacts") or []:
            if item.get("path"):
                add(str(item["path"]))
    return hints[:20]
