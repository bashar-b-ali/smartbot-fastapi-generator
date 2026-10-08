from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any

from app.services.model_pipeline.artifacts import (
    canonical_path as _normalized_canonical_path,
)
from app.services.model_pipeline.artifacts import (
    normalize_artifact_contract,
    normalize_custom_route,
)
from app.services.model_pipeline.intent import ARTIFACT_KEYS, user_request_text
from app.services.model_pipeline.naming import (
    class_name as _normalized_class_name,
)
from app.services.model_pipeline.naming import (
    identifier as _normalized_identifier,
)
from app.services.model_pipeline.naming import (
    plural as _normalized_plural,
)
from app.services.model_pipeline.naming import (
    singular as _normalized_singular,
)

RESOURCE_ALLOWLIST_PATTERNS = (
    r"\bresources?\s+(?:are|is|include|includes|included|:)\s+([^.;\n]+)",
    r"\bit\s+should\s+manage\s+([^.;\n]+)",
    r"(?:^|[.!?]\s*)manage\s+([^.;\n]+)",
)


def build_canonical_project_spec(
    *,
    prompt: str,
    schema_plan: dict[str, Any],
    api_contract: dict[str, Any],
    prompt_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = user_request_text(prompt)
    schema_tables = _tables_from_schema_plan(schema_plan)
    contract = _artifact_contract(api_contract)
    resources = _resources_from_tables(schema_tables, contract)
    operations = _operations_from_contract(contract)
    operations.extend(_crud_operations_for_resources(prompt, resources, contract))
    operations.extend(_custom_operations(prompt, resources, prompt_intent, contract))
    artifact_contract = _artifact_contract_from_resources(resources, operations, contract)
    allowlist = literal_resource_allowlist(prompt)
    return _canonical_spec(
        kind="project",
        prompt=prompt,
        resources=resources,
        operations=operations,
        artifact_contract=artifact_contract,
        allowed_resources=allowlist,
    )


def build_canonical_edit_spec(
    *,
    prompt: str,
    index: dict[str, Any],
    schema_delta: dict[str, Any],
    api_delta: dict[str, Any],
    prompt_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = user_request_text(prompt)
    allowlist = literal_resource_allowlist(prompt)
    schema_tables = _merge_table_lists(_tables_from_index(index), _tables_from_schema_plan(schema_delta))
    contract = _artifact_contract(api_delta)
    resources = _resources_from_tables(schema_tables, contract)
    operations = _operations_from_contract(contract)
    operations.extend(_crud_operations_for_resources(prompt, resources, contract, allowed_resources=allowlist))
    operations.extend(_custom_operations(prompt, resources, prompt_intent, contract))
    artifact_contract = _artifact_contract_from_resources(resources, operations, contract)
    return _canonical_spec(
        kind="edit",
        prompt=prompt,
        resources=resources,
        operations=operations,
        artifact_contract=artifact_contract,
        allowed_resources=allowlist,
    )


def merge_canonical_spec_into_contract(contract: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    merged = dict(contract)
    additions = _artifact_contract(spec)
    for key in ARTIFACT_KEYS:
        merged[key] = _merge_artifact_values(key, merged.get(key) or [], additions.get(key) or [])
    if additions.get("custom_routes"):
        merged["custom_routes"] = _merge_custom_routes(merged.get("custom_routes") or [], additions["custom_routes"])
    return merged


def enrich_schema_plan_from_canonical_spec(schema_plan: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    resources = [item for item in spec.get("resources") or [] if isinstance(item, dict)]
    if not resources:
        return schema_plan
    updated = dict(schema_plan)
    raw_schema = updated.get("schema")
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else {}
    tables = [dict(table) for table in schema.get("tables") or [] if isinstance(table, dict)]
    by_key = {_table_key(table): table for table in tables if _table_key(table)}
    changed = False
    for resource in resources:
        table_name = _plural(str(resource.get("table") or ""))
        if not table_name:
            continue
        table = by_key.get(table_name)
        if table is None:
            table = {"name": table_name, "columns": [{"name": "id", "type": "INTEGER"}]}
            by_key[table_name] = table
            tables.append(table)
            changed = True
        columns = [dict(column) for column in table.get("columns") or table.get("fields") or [] if isinstance(column, dict)]
        column_names = {_identifier(column.get("name") or column.get("field") or column.get("column")) for column in columns}
        for field in resource.get("fields") or []:
            if not isinstance(field, dict):
                continue
            field_name = _identifier(field.get("name") or field.get("field"))
            if not field_name:
                continue
            if field_name in column_names:
                for column in columns:
                    if _identifier(column.get("name") or column.get("field") or column.get("column")) != field_name:
                        continue
                    if field.get("filterable"):
                        column["filter"] = True
                        column["index"] = True
                    if field.get("references") and not (column.get("references") or column.get("foreign_key")):
                        column["references"] = field["references"]
                    if field.get("type") and not column.get("type"):
                        column["type"] = field["type"]
                    break
                continue
            column = {"name": field_name, "type": field.get("type") or _field_type_from_name(field_name)}
            if field.get("filterable"):
                column["filter"] = True
                column["index"] = True
            if field.get("references"):
                column["references"] = field["references"]
            columns.append(column)
            column_names.add(field_name)
            changed = True
        table["columns"] = columns
    if changed:
        schema["tables"] = tables
        updated["schema"] = schema
        if schema_plan.get("no_schema_change"):
            updated["no_schema_change"] = False
    return updated


def literal_resource_allowlist(prompt: str) -> list[str]:
    text = str(prompt or "")
    values: list[str] = []
    for pattern in RESOURCE_ALLOWLIST_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            segment = re.split(
                r"\b(?:with|where|that|which|can|should|keep|attach|filters?|actions?|endpoints?)\b",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            segment = segment.replace("/", " ")
            for raw_part in re.split(r",|\band\b|&", segment, flags=re.IGNORECASE):
                candidate = _plural(raw_part)
                if candidate and candidate not in values:
                    values.append(candidate)
    return values


def apply_resource_allowlist(
    *,
    prompt: str,
    schema_plan: dict[str, Any],
    api_contract: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    allowed = literal_resource_allowlist(prompt)
    if not allowed:
        return schema_plan, api_contract, {"allowed_resources": [], "rejections": []}

    rejections: list[dict[str, Any]] = []
    allowed_set = set(allowed)
    schema_plan = _filter_schema_plan(schema_plan, allowed_set, rejections)
    api_contract = _filter_api_contract(api_contract, allowed_set, rejections)
    return schema_plan, api_contract, {"allowed_resources": allowed, "rejections": rejections}


def filter_file_plan_for_allowed_resources(
    file_plan: dict[str, Any],
    allowed_resources: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not allowed_resources:
        return file_plan, []
    allowed_set = set(allowed_resources)
    rejections: list[dict[str, Any]] = []
    updated = dict(file_plan)
    files: list[dict[str, Any]] = []
    for item in file_plan.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if _file_path_allowed_by_resources(path, allowed_set):
            files.append(item)
        else:
            rejections.append({"kind": "file", "path": path, "reason": "outside_literal_resource_allowlist"})
    updated["files"] = files
    return updated, rejections


def _canonical_spec(
    *,
    kind: str,
    prompt: str,
    resources: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    artifact_contract: dict[str, Any],
    allowed_resources: list[str] | None = None,
) -> dict[str, Any]:
    resources = _dedupe_resources(resources)
    operations = _dedupe_operations(operations)
    return {
        "kind": kind,
        "summary": {
            "resource_count": len(resources),
            "operation_count": len(operations),
            "has_custom_behavior": any(op.get("kind") not in {"create", "list", "detail", "update", "delete"} for op in operations),
        },
        "request_terms": _request_terms(prompt),
        "allowed_resources": allowed_resources or [],
        "resources": resources,
        "operations": operations,
        "artifact_contract": artifact_contract,
    }


def _filter_schema_plan(schema_plan: dict[str, Any], allowed: set[str], rejections: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(schema_plan)
    raw_schema = updated.get("schema")
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else {}
    tables: list[dict[str, Any]] = []
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        name = _table_key(table)
        if not name or name in allowed:
            tables.append(table)
        else:
            rejections.append({"kind": "table", "table": name, "reason": "outside_literal_resource_allowlist"})
    schema["tables"] = tables
    schema["no_database_required"] = not bool(tables)
    updated["schema"] = schema
    if schema_plan.get("ddl_sql") and rejections:
        updated["ddl_sql"] = ""
    return updated


def _filter_api_contract(api_contract: dict[str, Any], allowed: set[str], rejections: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(api_contract)
    artifact = _artifact_contract(updated)
    filtered_artifact = _filter_artifact_contract(artifact, allowed, rejections)
    updated["artifact_contract"] = filtered_artifact
    if isinstance(updated.get("routes"), list):
        routes: list[dict[str, Any]] = []
        for route in updated["routes"]:
            if not isinstance(route, dict):
                continue
            path = _canonical_path(route.get("path") or route.get("route"))
            if _route_allowed_by_resources(path, allowed, filtered_artifact.get("custom_routes") or []):
                routes.append(route)
            else:
                rejections.append({"kind": "route", "path": path, "reason": "outside_literal_resource_allowlist"})
        updated["routes"] = routes
    return updated


def _filter_artifact_contract(contract: dict[str, Any], allowed: set[str], rejections: list[dict[str, Any]]) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, values in contract.items():
        if key == "custom_routes":
            allowed_values = []
            for item in values or []:
                if not isinstance(item, dict):
                    continue
                path = _canonical_path(item.get("path"))
                if _route_allowed_by_resources(path, allowed, []):
                    allowed_values.append(item)
                else:
                    rejections.append({"kind": "custom_route", "path": path, "reason": "outside_literal_resource_allowlist"})
            if allowed_values:
                filtered[key] = allowed_values
            continue
        if key not in ARTIFACT_KEYS:
            filtered[key] = values
            continue
        allowed_values = []
        for item in values or []:
            if not isinstance(item, dict):
                continue
            if _artifact_item_allowed(key, item, allowed, contract.get("custom_routes") or []):
                allowed_values.append(_normalize_allowed_artifact_item(key, item))
            else:
                rejections.append({"kind": key, "item": item, "reason": "outside_literal_resource_allowlist"})
        if allowed_values:
            filtered[key] = allowed_values
    return filtered


def _normalize_allowed_artifact_item(key: str, item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if key in {"required_tables", "required_fields", "required_filters", "required_behaviors"} and normalized.get("table"):
        normalized["table"] = _plural(str(normalized["table"]))
    if key == "required_relationships":
        if normalized.get("from_table"):
            normalized["from_table"] = _plural(str(normalized["from_table"]))
        if normalized.get("to_table"):
            normalized["to_table"] = _plural(str(normalized["to_table"]))
    return normalized


def _artifact_item_allowed(key: str, item: dict[str, Any], allowed: set[str], custom_routes: list[dict[str, Any]]) -> bool:
    if key == "required_routes":
        return _route_allowed_by_resources(_canonical_path(item.get("path")), allowed, custom_routes)
    if key in {"required_fields", "required_filters", "required_tables"}:
        table = _plural(str(item.get("table") or item.get("model") or item.get("name") or ""))
        return not table or table in allowed
    if key == "required_relationships":
        from_table = _plural(str(item.get("from_table") or item.get("table") or ""))
        to_table = _plural(str(item.get("to_table") or item.get("references_table") or ""))
        return (not from_table or from_table in allowed) and (not to_table or to_table in allowed)
    if key == "required_behaviors":
        table = _plural(str(item.get("table") or ""))
        return not table or table in allowed
    return True


def _route_allowed_by_resources(path: str, allowed: set[str], custom_routes: list[dict[str, Any]]) -> bool:
    path = _canonical_path(path)
    if not path:
        return True
    first_literal = _table_from_route_path(path)
    if first_literal in {"auths", "healths", "pings", "files", "uploads", "websockets", "ws"}:
        return True
    if first_literal not in allowed:
        return False
    if any(_canonical_path(route.get("path")) == path for route in custom_routes if isinstance(route, dict)):
        return True
    parts = [part for part in path.strip("/").split("/") if part]
    literal_parts = [part for part in parts if not part.startswith("{")]
    return len(literal_parts) == 1


def _file_path_allowed_by_resources(path: str, allowed: set[str]) -> bool:
    normalized = str(path or "").replace("\\", "/").lower()
    generic = {
        "main.py",
        "database.py",
        "models.py",
        "model.py",
        "schemas.py",
        "requirements.txt",
        "auth.py",
        "websockets.py",
        "websocket.py",
        "files.py",
        "routers/__init__.py",
    }
    if normalized in generic:
        return True
    if normalized.startswith("routers/"):
        name = normalized.rsplit("/", 1)[-1].removesuffix(".py")
        return _plural(name) in allowed
    return True


def _tables_from_schema_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw_schema = plan.get("schema")
    schema = raw_schema if isinstance(raw_schema, dict) else {}
    return [table for table in schema.get("tables") or [] if isinstance(table, dict)]


def _tables_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    raw_schema = index.get("database_schema")
    schema = raw_schema if isinstance(raw_schema, dict) else {}
    tables: list[dict[str, Any]] = []
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        tables.append(
            {
                "name": table.get("db_table_name") or table.get("name"),
                "columns": table.get("fields") or [],
            }
        )
    return tables


def _merge_table_lists(base: list[dict[str, Any]], delta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for table in [*base, *delta]:
        key = _table_key(table)
        if not key:
            continue
        current = merged.setdefault(key, {"name": key, "columns": []})
        columns = [dict(column) for column in current.get("columns") or [] if isinstance(column, dict)]
        seen = {_identifier(column.get("name") or column.get("field") or column.get("column")) for column in columns}
        for column in table.get("columns") or table.get("fields") or []:
            if not isinstance(column, dict):
                continue
            name = _identifier(column.get("name") or column.get("field") or column.get("column"))
            if name and name not in seen:
                columns.append(dict(column, name=name))
                seen.add(name)
        current["columns"] = columns
    return list(merged.values())


def _artifact_contract(data: dict[str, Any]) -> dict[str, Any]:
    contract = data.get("artifact_contract") if isinstance(data.get("artifact_contract"), dict) else data
    return normalize_artifact_contract(contract if isinstance(contract, dict) else {})


def _resources_from_tables(tables: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    by_table: dict[str, dict[str, Any]] = {}
    for table in tables:
        table_name = _plural(str(table.get("name") or table.get("table") or table.get("db_table_name") or table.get("model") or ""))
        if not table_name:
            continue
        resource = by_table.setdefault(table_name, {"table": table_name, "model": _class_name(table_name), "fields": [], "filters": []})
        for column in table.get("columns") or table.get("fields") or []:
            field = _field_from_column(column)
            if field:
                _append_field(resource, field)
        _apply_schema_relationships(resource, table)
    for item in contract.get("required_tables") or []:
        if not isinstance(item, dict):
            continue
        table_name = _plural(str(item.get("table") or item.get("name") or ""))
        if table_name:
            by_table.setdefault(table_name, {"table": table_name, "model": _class_name(table_name), "fields": [], "filters": []})
    for item in contract.get("required_fields") or []:
        if not isinstance(item, dict):
            continue
        table_name = _plural(str(item.get("table") or item.get("model") or ""))
        field_name = _identifier(item.get("field") or item.get("name"))
        if table_name and field_name:
            resource = by_table.setdefault(table_name, {"table": table_name, "model": _class_name(table_name), "fields": [], "filters": []})
            _append_field(resource, {"name": field_name, "type": _field_type_from_name(field_name)})
    for item in contract.get("required_filters") or []:
        if not isinstance(item, dict):
            continue
        table_name = _plural(str(item.get("table") or item.get("model") or ""))
        field_name = _identifier(item.get("field") or item.get("name"))
        if table_name and field_name:
            resource = by_table.setdefault(table_name, {"table": table_name, "model": _class_name(table_name), "fields": [], "filters": []})
            _append_filter(resource, field_name)
            _append_field(resource, {"name": field_name, "type": _field_type_from_name(field_name), "filterable": True})
    resources.extend(by_table.values())
    return resources


def _relationship_field_name(relationship: dict[str, Any]) -> str:
    column = relationship.get("column") or relationship.get("field") or relationship.get("name")
    if column:
        return _identifier(column)
    columns = relationship.get("columns")
    if isinstance(columns, list) and columns:
        return _identifier(columns[0])
    return ""


def _relationship_target(relationship: dict[str, Any]) -> str:
    return _plural(str(
        relationship.get("related_table")
        or relationship.get("to_table")
        or relationship.get("target_table")
        or relationship.get("references_table")
        or relationship.get("to")
        or ""
    ))


def _apply_schema_relationships(resource: dict[str, Any], table: dict[str, Any]) -> None:
    table_name = _plural(str(table.get("name") or table.get("table") or ""))
    if not table_name:
        return
    for relationship in table.get("relationships") or []:
        if not isinstance(relationship, dict):
            continue
        source = _plural(str(relationship.get("table") or relationship.get("from_table") or table_name))
        if source and source != table_name:
            continue
        field_name = _relationship_field_name(relationship)
        target = _relationship_target(relationship)
        if not field_name or not target:
            continue
        _append_field(resource, {"name": field_name, "type": _field_type_from_name(field_name), "references": f"{target}.id"})


def _field_from_column(column: Any) -> dict[str, Any] | None:
    if not isinstance(column, dict):
        name = _identifier(column)
        return {"name": name, "type": _field_type_from_name(name)} if name else None
    name = _identifier(column.get("name") or column.get("field") or column.get("column"))
    if not name:
        return None
    return {
        "name": name,
        "type": column.get("type") or column.get("kind") or _field_type_from_name(name),
        "filterable": bool(column.get("filter") or column.get("filterable") or column.get("searchable") or column.get("index") or column.get("indexed")),
        "references": column.get("references") or column.get("foreign_key"),
    }


def _operations_from_contract(contract: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for route in contract.get("required_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "GET").upper()
        path = _canonical_path(route.get("path"))
        table = _table_from_route_path(path)
        operations.append({"kind": _operation_kind(method, path), "method": method, "path": path, "table": table, "source": "model_contract"})
    for route in contract.get("custom_routes") or []:
        if isinstance(route, dict):
            operations.append(dict(route, source="custom_route"))
    for item in contract.get("required_behaviors") or []:
        if isinstance(item, dict):
            behavior = _identifier(item.get("behavior") or item.get("kind") or item.get("name"))
            if behavior:
                operations.append({"kind": behavior, "table": _plural(str(item.get("table") or "")), "source": "model_contract"})
    return operations


def _crud_operations_for_resources(
    prompt: str,
    resources: list[dict[str, Any]],
    contract: dict[str, Any],
    *,
    allowed_resources: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not resources:
        return []
    text = prompt.lower()
    requested_actions = _requested_actions(text)
    if not requested_actions:
        return []
    operations: list[dict[str, Any]] = []
    allowed = set(allowed_resources or [])
    for resource in resources:
        table = _plural(str(resource.get("table") or ""))
        if not table:
            continue
        if allowed and table not in allowed:
            continue
        base = f"/{table}"
        item = f"{base}/{{{_singular(table)}_id}}"
        route_by_action = {
            "create": {"kind": "create", "method": "POST", "path": base, "table": table, "source": "canonical_crud"},
            "list": {"kind": "list", "method": "GET", "path": base, "table": table, "source": "canonical_crud"},
            "detail": {"kind": "detail", "method": "GET", "path": item, "table": table, "source": "canonical_crud"},
            "update": {"kind": "update", "method": "PUT", "path": item, "table": table, "source": "canonical_crud"},
            "delete": {"kind": "delete", "method": "DELETE", "path": item, "table": table, "source": "canonical_crud"},
        }
        for action in ("create", "list", "detail", "update", "delete"):
            if action in requested_actions or (not requested_actions and contract.get("required_tables")):
                operations.append(route_by_action[action])
    return operations


def _requested_actions(text: str) -> set[str]:
    if "crud" in text or "manage" in text:
        return {"create", "list", "detail", "update", "delete"}
    actions: set[str] = set()
    aliases = {
        "create": ("create", "post", "add"),
        "list": ("list", "index", "browse"),
        "detail": ("detail", "retrieve", "read", "get"),
        "update": ("update", "put", "patch", "edit"),
        "delete": ("delete", "remove", "destroy"),
    }
    explicit_action_text = bool(re.search(r"\bactions?\b|\bwith\b.*\b(?:create|list|detail|retrieve|update|delete)\b", text))
    if explicit_action_text:
        for action, names in aliases.items():
            if any(re.search(rf"\b{name}\b", text) for name in names):
                actions.add(action)
    if actions:
        return actions
    if "resource" in text or "resources" in text:
        return {"create", "list", "detail", "update", "delete"}
    return set()


def _custom_operations(
    prompt: str,
    resources: list[dict[str, Any]],
    prompt_intent: dict[str, Any] | None,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    known_tables = {_plural(str(resource.get("table") or "")) for resource in resources if resource.get("table")}
    for route in (prompt_intent or {}).get("custom_routes") or []:
        if isinstance(route, dict):
            operations.append(dict(route, source="prompt_intent"))
    text = prompt.lower().replace("-", " ")
    top_match = re.search(r"top\s+(\d+)\s+([a-z0-9_]+)\s+that\s+have\s+([a-z0-9_]+)", text)
    if top_match:
        parent = _resolve_table(top_match.group(2), known_tables)
        child = _resolve_table(top_match.group(3), known_tables)
        operations.append(
            {
                "kind": "top_related",
                "method": "GET",
                "path": f"/{parent}/top-by-{child}",
                "table": parent,
                "parent_table": parent,
                "child_table": child,
                "limit": int(top_match.group(1)),
                "required_behaviors": [{"behavior": "top_n", "table": parent}],
                "source": "canonical_prompt",
            }
        )
    page_match = re.search(r"paginate\s+(?:the\s+)?([a-z0-9_]+).*?(?:for|per)\s+each\s+([a-z0-9_]+)", text)
    if page_match:
        child = _resolve_table(page_match.group(1), known_tables)
        parent = _resolve_table(page_match.group(2), known_tables)
        page_size_match = re.search(r"(?:paginate|pagination)[^\d]*(\d+)", text)
        page_size = int(page_size_match.group(1)) if page_size_match else 10
        parent_id = f"{_singular(parent)}_id"
        operations.append(
            {
                "kind": "parent_children_paginated",
                "method": "GET",
                "path": f"/{parent}/{{{parent_id}}}/{child}",
                "table": child,
                "parent_table": parent,
                "child_table": child,
                "page_size": page_size,
                "required_filters": [{"table": child, "field": parent_id}],
                "required_behaviors": [{"behavior": "pagination", "table": child}],
                "source": "canonical_prompt",
            }
        )
    operations.extend(_operations_from_contract({"custom_routes": contract.get("custom_routes") or []}))
    return operations


def _artifact_contract_from_resources(
    resources: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    base_contract: dict[str, Any],
) -> dict[str, Any]:
    contract = {key: list(base_contract.get(key) or []) for key in ARTIFACT_KEYS}
    contract["custom_routes"] = list(base_contract.get("custom_routes") or [])
    for resource in resources:
        table = _plural(str(resource.get("table") or ""))
        if not table:
            continue
        _append_artifact(contract, "required_tables", {"table": table}, ("table",))
        for field in resource.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = _identifier(field.get("name") or field.get("field"))
            if not name:
                continue
            _append_artifact(contract, "required_fields", {"table": table, "field": name}, ("table", "field"))
            if field.get("filterable") or name in set(resource.get("filters") or []):
                _append_artifact(contract, "required_filters", {"table": table, "field": name}, ("table", "field"))
            target = field.get("references")
            if target:
                _append_artifact(
                    contract,
                    "required_relationships",
                    {"from_table": table, "field": name, "to_table": _plural(str(target).split(".", 1)[0].split("(", 1)[0])},
                    ("from_table", "field", "to_table"),
                )
    for op in operations:
        if not isinstance(op, dict):
            continue
        method = str(op.get("method") or "").upper()
        path = _canonical_path(op.get("path"))
        if method and path:
            _append_artifact(contract, "required_routes", {"method": method, "path": path}, ("method", "path"))
        for item in op.get("required_filters") or []:
            if isinstance(item, dict):
                _append_artifact(contract, "required_filters", item, ("table", "field"))
                _append_artifact(contract, "required_fields", item, ("table", "field"))
        for item in op.get("required_fields") or []:
            if isinstance(item, dict):
                _append_artifact(contract, "required_fields", item, ("table", "field"))
        for item in op.get("required_behaviors") or []:
            if isinstance(item, dict):
                _append_artifact(contract, "required_behaviors", item, ("behavior", "table"))
        if op.get("kind") in {"top_n", "top_related"}:
            _append_artifact(contract, "required_behaviors", {"behavior": "top_n", "table": op.get("parent_table") or op.get("table")}, ("behavior", "table"))
        if op.get("kind") in {"pagination", "parent_children_paginated"}:
            _append_artifact(contract, "required_behaviors", {"behavior": "pagination", "table": op.get("child_table") or op.get("table")}, ("behavior", "table"))
        if op.get("kind") in {"top_related", "parent_children_paginated", "aggregate_count_by_field"}:
            contract["custom_routes"] = _merge_custom_routes(contract.get("custom_routes") or [], [op])
    return {key: value for key, value in contract.items() if value}


def _append_field(resource: dict[str, Any], field: dict[str, Any]) -> None:
    values = resource.setdefault("fields", [])
    name = _identifier(field.get("name") or field.get("field"))
    if not name:
        return
    for index, existing in enumerate(values):
        if _identifier(existing.get("name") or existing.get("field")) == name:
            values[index] = {**existing, **{key: value for key, value in field.items() if value not in (None, "", [])}, "name": name}
            if values[index].get("filterable"):
                _append_filter(resource, name)
            return
    item = {key: value for key, value in field.items() if value not in (None, "", [])}
    item["name"] = name
    values.append(item)
    if item.get("filterable"):
        _append_filter(resource, name)


def _append_filter(resource: dict[str, Any], field: str) -> None:
    field = _identifier(field)
    filters = resource.setdefault("filters", [])
    if field and field not in filters:
        filters.append(field)


def _append_artifact(contract: dict[str, list[dict[str, Any]]], key: str, item: dict[str, Any], keys: tuple[str, ...]) -> None:
    values = contract.setdefault(key, [])
    normalized = {name: value for name, value in item.items() if value not in (None, "", [])}
    if not normalized:
        return
    marker = tuple(str(normalized.get(name) or "").strip().lower() for name in keys)
    if any(tuple(str(existing.get(name) or "").strip().lower() for name in keys) == marker for existing in values if isinstance(existing, dict)):
        return
    values.append(normalized)


def _merge_artifact_values(key: str, existing: list[Any], additions: list[Any]) -> list[dict[str, Any]]:
    marker_keys = _marker_keys(key)
    merged = [item for item in existing if isinstance(item, dict)]
    for item in additions:
        if isinstance(item, dict):
            _append_artifact({key: merged}, key, item, marker_keys)
    return merged


def _merge_custom_routes(existing: list[Any], additions: list[Any]) -> list[dict[str, Any]]:
    merged = [normalize_custom_route(item) for item in existing if isinstance(item, dict)]
    seen = {(_identifier(item.get("kind")), str(item.get("method") or "").upper(), _canonical_path(item.get("path"))) for item in merged}
    for item in additions:
        if not isinstance(item, dict):
            continue
        normalized = normalize_custom_route(item)
        marker = (_identifier(normalized.get("kind")), str(normalized.get("method") or "").upper(), _canonical_path(normalized.get("path")))
        if marker not in seen:
            merged.append(normalized)
            seen.add(marker)
    return merged


def _dedupe_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_table: dict[str, dict[str, Any]] = {}
    for resource in resources:
        table = _plural(str(resource.get("table") or ""))
        if not table:
            continue
        target = by_table.setdefault(table, {"table": table, "model": resource.get("model") or _class_name(table), "fields": [], "filters": []})
        for field in resource.get("fields") or []:
            if isinstance(field, dict):
                _append_field(target, field)
        for field in resource.get("filters") or []:
            _append_filter(target, str(field))
    return list(by_table.values())


def _dedupe_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for operation in operations:
        kind = _identifier(operation.get("kind"))
        marker = (kind, str(operation.get("method") or "").upper(), _canonical_path(operation.get("path")), _plural(str(operation.get("table") or operation.get("parent_table") or "")))
        if marker in seen:
            continue
        result.append({key: value for key, value in operation.items() if value not in (None, "", [])})
        seen.add(marker)
    return result


def _request_terms(prompt: str) -> list[str]:
    terms = []
    for term in ("crud", "auth", "jwt", "websocket", "pagination", "paginate", "top", "filter", "upload"):
        if term in prompt.lower():
            terms.append(term)
    return terms


def _operation_kind(method: str, path: str) -> str:
    if method == "POST":
        return "create"
    if method in {"PUT", "PATCH"}:
        return "update"
    if method == "DELETE":
        return "delete"
    if "{" in path:
        return "detail"
    return "list"


def _table_from_route_path(path: str) -> str:
    for part in path.strip("/").split("/"):
        if part and not part.startswith("{"):
            return _plural(part)
    return ""


def _marker_keys(key: str) -> tuple[str, ...]:
    if key == "required_routes":
        return ("method", "path")
    if key in {"required_fields", "required_filters"}:
        return ("table", "field")
    if key == "required_relationships":
        return ("from_table", "field", "to_table")
    if key == "required_behaviors":
        return ("behavior", "table")
    return ("table",)


def _table_key(table: dict[str, Any]) -> str:
    return _plural(str(table.get("name") or table.get("table") or table.get("db_table_name") or table.get("model") or ""))


def _resolve_table(value: str, known_tables: set[str]) -> str:
    table = _plural(value)
    if table in known_tables:
        return table
    for candidate in known_tables:
        if _singular(candidate) == _singular(table):
            return candidate
    candidates = [candidate for candidate in known_tables if abs(len(candidate) - len(table)) <= 3]
    matches = get_close_matches(table, candidates, n=1, cutoff=0.72)
    return matches[0] if matches else table


def _field_type_from_name(field: str) -> str:
    field = _identifier(field)
    if any(term in field for term in ("price", "amount", "cost", "total", "score", "rate")):
        return "float"
    if field.startswith("is_") or field in {"paid", "done", "enabled", "approved", "active", "published"}:
        return "BOOLEAN"
    if field.endswith("_id") or field in {"count", "quantity", "limit", "page"}:
        return "INTEGER"
    if "date" in field:
        return "TIMESTAMP"
    return "TEXT"


def _canonical_path(path: Any) -> str:
    value = _normalized_canonical_path(path)
    return "" if value == "/" and not str(path or "").strip() else value


def _class_name(value: str) -> str:
    return _normalized_class_name(value, fallback="Record")


def _identifier(value: Any) -> str:
    return _normalized_identifier(value, numeric_prefix="field")


def _singular(value: str) -> str:
    return _normalized_singular(value)


def _plural(value: str) -> str:
    return _normalized_plural(value)
