from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any

from app.services.model_pipeline.naming import (
    identifier as _normalized_identifier,
)
from app.services.model_pipeline.naming import (
    plural as _normalized_plural,
)
from app.services.model_pipeline.naming import (
    singular as _normalized_singular,
)

ARTIFACT_KEYS = (
    "required_routes",
    "required_tables",
    "required_fields",
    "required_filters",
    "required_relationships",
    "required_behaviors",
)


def user_request_text(prompt: str) -> str:
    raw = str(prompt or "")
    if not re.search(
        r"Conversation-derived backend change request|Backend change request derived from recent conversation|User messages, oldest to newest|User-authored request messages follow",
        raw,
        flags=re.IGNORECASE,
    ):
        return raw

    messages: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"(?:User message\s+\d+\s*[-.:]\s*|\d+\.\s+)(.+)", stripped, flags=re.IGNORECASE)
        if match:
            messages.append(match.group(1).strip())
    return "\n".join(messages) if messages else raw


def extract_prompt_intent(prompt: str, *, existing_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    request_text = user_request_text(prompt)
    text = _normalize_text(request_text)
    resources = _extract_resources(text)
    filters = _extract_filters(text, resources=resources, existing_schema=existing_schema)
    added_fields = _extract_added_fields(text, resources, existing_schema=existing_schema)
    added_pairs = [
        (table, field)
        for table, table_fields in added_fields.items()
        for field in table_fields
    ]
    if "filter by it" in text and len(added_pairs) == 1:
        table, field = added_pairs[0]
        filters.setdefault(table, []).append(field)
    known_tables = set(resources) | _schema_tables(existing_schema)
    explicit_routes = _extract_explicit_routes(request_text)
    custom_routes = _extract_custom_routes(text, resources, known_tables)
    custom_routes.extend(_extract_book_commerce_routes(text, known_tables | set(resources)))
    custom_routes.extend(explicit_routes)
    public = _has_public_resource_intent(text)
    auth = _has_auth_intent(text)
    websocket = "websocket" in text or "web socket" in text
    upload_requested = any(term in text for term in ("file upload", "upload file", "multipart"))
    download_requested = any(term in text for term in ("download", "csv report", "text file"))
    files = upload_requested or download_requested
    contract = _empty_contract()
    schema_additions: dict[str, list[dict[str, Any]]] = {"tables": []}

    for resource in resources:
        _append_unique(contract, "required_tables", {"table": resource}, ("table",))
        _append_crud_routes(contract, resource, _requested_actions(text))
    for table, fields in filters.items():
        _append_unique(contract, "required_tables", {"table": table}, ("table",))
        for field in fields:
            reference_table = _reference_table_for_field(field, known_tables)
            _append_unique(contract, "required_fields", {"table": table, "field": field}, ("table", "field"))
            _append_unique(contract, "required_filters", {"table": table, "field": field}, ("table", "field"))
            if reference_table:
                _append_unique(
                    contract,
                    "required_relationships",
                    {"from_table": table, "field": field, "to_table": reference_table},
                    ("from_table", "field", "to_table"),
                )
            _add_schema_field(
                schema_additions,
                table,
                field,
                field_type=_field_type_from_name(field, context=text),
                filterable=True,
                references=f"{reference_table}(id)" if reference_table else None,
            )
    for table, fields in added_fields.items():
        _append_unique(contract, "required_tables", {"table": table}, ("table",))
        for field in fields:
            reference_table = _reference_table_for_field(field, known_tables)
            _append_unique(contract, "required_fields", {"table": table, "field": field}, ("table", "field"))
            if reference_table:
                _append_unique(
                    contract,
                    "required_relationships",
                    {"from_table": table, "field": field, "to_table": reference_table},
                    ("from_table", "field", "to_table"),
                )
            _add_schema_field(
                schema_additions,
                table,
                field,
                field_type=_field_type_from_name(field, context=text),
                references=f"{reference_table}(id)" if reference_table else None,
            )
    for route in custom_routes:
        for item in route.get("required_tables") or []:
            _append_unique(contract, "required_tables", item, ("table",))
            table = str(item.get("table") or "")
            if table:
                _add_schema_field(schema_additions, table, "id", field_type="INTEGER")
        _append_unique(
            contract,
            "required_routes",
            {"method": route["method"], "path": route["path"]},
            ("method", "path"),
        )
        for item in route.get("required_filters") or []:
            _append_unique(contract, "required_filters", item, ("table", "field"))
            _append_unique(contract, "required_fields", item, ("table", "field"))
            _add_schema_field(
                schema_additions,
                str(item["table"]),
                str(item["field"]),
                field_type=_field_type_from_name(str(item["field"]), context=text),
                filterable=True,
                references=item.get("references"),
            )
        for item in route.get("required_fields") or []:
            _append_unique(contract, "required_fields", item, ("table", "field"))
            _add_schema_field(
                schema_additions,
                str(item["table"]),
                str(item["field"]),
                field_type=_field_type_from_name(str(item["field"]), context=text),
                references=item.get("references"),
            )
        for item in route.get("required_relationships") or []:
            _append_unique(contract, "required_relationships", item, ("from_table", "field", "to_table"))
        for behavior in route.get("required_behaviors") or []:
            _append_unique(contract, "required_behaviors", behavior, ("behavior", "table"))
    if websocket:
        _append_unique(contract, "required_behaviors", {"behavior": "websocket"}, ("behavior", "table"))
        if any(term in text for term in ("notification", "notifications", "alert", "alerts", "broadcast")):
            _append_unique(contract, "required_behaviors", {"behavior": "websocket_notification"}, ("behavior", "table"))
    if upload_requested:
        _append_unique(contract, "required_behaviors", {"behavior": "file_upload"}, ("behavior", "table"))
    if download_requested:
        download_format = "csv" if "csv" in text else "text"
        download_routes = [
            route
            for route in explicit_routes
            if route.get("method") == "GET" and "download" in str(route.get("path") or "")
        ]
        for route in download_routes or [{}]:
            path = str(route.get("path") or "")
            data_table = next(
                (
                    table
                    for table in added_fields
                    if _singular(table) in path.lower() or table in path.lower()
                ),
                "",
            )
            if not data_table:
                path_tables = [
                    table
                    for table in [*resources, *sorted(known_tables)]
                    if _singular(table) in path.lower() or table in path.lower()
                ]
                data_table = path_tables[-1] if path_tables else resources[0] if resources else ""
            behavior = {
                "behavior": "file_download",
                "format": download_format,
                "method": route.get("method") or "GET",
                "path": path,
            }
            if path and data_table:
                behavior["table"] = data_table
                behavior["fields"] = list(added_fields.get(data_table) or [])
            _append_unique(
                contract,
                "required_behaviors",
                behavior,
                ("behavior", "path"),
            )
    for access_rule in _extract_access_rules(text, explicit_routes, known_tables | set(resources)):
        _append_unique(
            contract,
            "required_behaviors",
            access_rule,
            ("behavior", "table", "method", "path", "role"),
        )
    if auth and not public:
        _append_unique(contract, "required_behaviors", {"behavior": "auth_protected"}, ("behavior", "table"))

    return {
        "contract": {key: value for key, value in contract.items() if value},
        "schema_additions": schema_additions,
        "custom_routes": custom_routes,
        "auth_policy": {"public_resources": public, "auth_requested": auth, "files": files, "websocket": websocket},
    }


def merge_intent_into_schema_plan(schema_plan: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    additions = (intent.get("schema_additions") or {}).get("tables") or []
    if not additions:
        return schema_plan
    merged = dict(schema_plan)
    raw_schema = merged.get("schema")
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else {}
    tables = [dict(table) for table in schema.get("tables") or [] if isinstance(table, dict)]
    by_name = {_table_key(table): table for table in tables if _table_key(table)}
    for addition in additions:
        table_key = _table_key(addition)
        if not table_key:
            continue
        if table_key not in by_name:
            by_name[table_key] = {"name": _plural(table_key), "columns": [{"name": "id", "type": "INTEGER"}]}
            tables.append(by_name[table_key])
        _merge_columns(by_name[table_key], addition.get("columns") or [])
    schema["tables"] = tables
    merged["schema"] = schema
    if tables:
        merged["no_schema_change"] = False
    return merged


def merge_intent_into_api_contract(api_contract: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    additions = intent.get("contract") if isinstance(intent.get("contract"), dict) else {}
    if not additions:
        return api_contract
    merged = dict(api_contract)
    raw_contract = merged.get("artifact_contract")
    contract = dict(raw_contract) if isinstance(raw_contract, dict) else {}
    for key in ARTIFACT_KEYS:
        existing = [item for item in contract.get(key) or [] if isinstance(item, dict)]
        for item in additions.get(key) or []:
            if not isinstance(item, dict):
                continue
            marker_keys = _marker_keys(key)
            _append_unique_to_list(existing, item, marker_keys)
        contract[key] = existing
    if intent.get("custom_routes"):
        contract["custom_routes"] = list(contract.get("custom_routes") or []) + list(intent["custom_routes"])
    contract["auth_policy"] = intent.get("auth_policy") or {}
    merged["artifact_contract"] = contract
    if intent.get("auth_policy"):
        merged["auth_policy"] = intent["auth_policy"]
    return merged


def _extract_resources(text: str) -> list[str]:
    resources: list[str] = []
    patterns = [
        r"resources?\s+(?:are|is)\s+([a-z0-9_,\s]+?)(?:\.|;|$)",
        r"(?:manage|store|stores|resources include|it should manage)\s+([a-z0-9_,\s]+?)(?:\.|;|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            resource_phrase = re.split(
                r"\b(?:with|including|include|that|where|filter|filters|filtered|search|by)\b",
                match.group(1),
                maxsplit=1,
            )[0]
            for value in _split_list(resource_phrase):
                if value and value not in _STOP_RESOURCE_WORDS:
                    resources.append(_plural(value))
    for value in re.findall(r"\b([a-z][a-z0-9_]+)\s*:\s*[a-z][a-z0-9_,\s]*", text):
        resources.append(_plural(value))
    for value in re.findall(r"\badd\s+(?:a\s+|an\s+)?([a-z][a-z0-9_]+)\s+with\b", text):
        if value not in _STOP_RESOURCE_WORDS:
            resources.append(_plural(value))
    return list(dict.fromkeys(resources))


def _extract_filters(
    text: str,
    *,
    resources: list[str] | None = None,
    existing_schema: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    filters: dict[str, list[str]] = {}
    known_tables = set(resources or []) | _schema_tables(existing_schema)
    default_table = (resources or sorted(known_tables) or [""])[0]
    for match in re.finditer(r"filters?\s+(?:(?:for|by)\s+)?([a-z0-9_]+)\s*:\s*([a-z0-9_,\s]+)", text):
        table = _plural(match.group(1))
        fields = [
            field
            for field in (_field_identifier(value) for value in _split_list(match.group(2)))
            if _usable_field(field)
        ]
        if fields:
            filters.setdefault(table, [])
            filters[table].extend(fields)
    for match in re.finditer(r"\b(?:filter|filters|filtering)\s+by\s+([a-z0-9_,\s]+?)(?:\.|;|$)", text):
        prefix = text[max(0, match.start() - 80):match.start()]
        if re.search(r"\blist\s+[a-z0-9_]+\s+(?:can\s+)?$", prefix):
            continue
        fields = [
            field
            for field in (_field_identifier(value) for value in _split_list(match.group(1)))
            if _usable_field(field)
        ]
        if fields and default_table:
            filters.setdefault(default_table, [])
            filters[default_table].extend(fields)
    for match in re.finditer(r"\b(?:list|search|browse)\s+([a-z0-9_]+)\s+(?:can\s+)?(?:filter|filters|filtered|search)\s+by\s+([a-z0-9_,\s]+?)(?:\.|;|$)", text):
        table = _plural(match.group(1))
        fields = [
            field
            for field in (_field_identifier(value) for value in _split_list(match.group(2)))
            if _usable_field(field)
        ]
        if fields:
            filters.setdefault(table, [])
            filters[table].extend(fields)
    for match in re.finditer(r"\b([a-z0-9_]+)\s*:\s*([a-z0-9_,\s]+)", text):
        table = _plural(match.group(1))
        if known_tables and table not in known_tables:
            continue
        fields = [
            field
            for field in (_field_identifier(value) for value in _split_list(match.group(2)))
            if _usable_field(field)
        ]
        if fields:
            filters.setdefault(table, [])
            filters[table].extend(fields)
    return {table: list(dict.fromkeys(fields)) for table, fields in filters.items()}

def _extract_added_fields(
    text: str,
    resources: list[str],
    *,
    existing_schema: dict[str, Any] | None,
) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    known_tables = set(resources) | _schema_tables(existing_schema)
    field_words = r"[a-z0-9_,\s]+?"
    # "add a done field to the tasks table" must resolve to `tasks`, not to the
    # determiner in front of it.
    for match in re.finditer(
        rf"(?:add|include)\s+({field_words})\s+(?:to|for|on)\s+{_DETERMINER_PREFIX}([a-z0-9_]+)"
        r"(?:\s+(?:table|model|resource|entity))?",
        text,
    ):
        table = _plural(match.group(2))
        if table in _STOP_RESOURCE_WORDS:
            continue
        values = [value for value in (_field_identifier(value) for value in _split_list(match.group(1))) if _usable_field(value)]
        if values:
            fields.setdefault(table, [])
            fields[table].extend(values)
    for match in re.finditer(r"\b(?:with|include|including)\s+([a-z0-9_,\s]+?)\s+(?:fields?|columns?|attributes?)(?:\.|;|$)", text):
        if len(known_tables) == 1:
            table = next(iter(known_tables))
        elif resources:
            table = resources[0]
        else:
            continue
        values = [value for value in (_field_identifier(value) for value in _split_list(match.group(1))) if _usable_field(value)]
        if values:
            fields.setdefault(table, [])
            fields[table].extend(values)
    for match in re.finditer(r"\b([a-z0-9_]+)\s+with\s+([a-z0-9_,\s]+?)(?:\s+fields?)?(?:\.|;|$)", text):
        table = _plural(match.group(1))
        if table in _STOP_RESOURCE_WORDS:
            continue
        if known_tables and table not in known_tables:
            continue
        values = [value for value in (_field_identifier(value) for value in _split_list(match.group(2))) if _usable_field(value)]
        if values:
            fields.setdefault(table, [])
            fields[table].extend(values)
    for match in re.finditer(r"\b([a-z0-9_]+)\s+(?:have|has)\s+([a-z0-9_,\s]+?)(?:\s+fields?)?(?:\.|;|$)", text):
        table = _plural(match.group(1))
        if table in _STOP_RESOURCE_WORDS:
            continue
        if known_tables and table not in known_tables:
            continue
        values = [
            value
            for value in (_field_identifier(value) for value in _split_list(match.group(2)))
            if _usable_field(value)
        ]
        if values:
            fields.setdefault(table, [])
            fields[table].extend(values)
    for table in known_tables:
        singular_table = _singular(table)
        for match in re.finditer(rf"\b([a-z0-9_]+)\s+(?:field|attr|attribute|column)\s+(?:to|for|on)\s+{singular_table}s?\b", text):
            field_name = _field_identifier(match.group(1))
            if _usable_field(field_name):
                fields.setdefault(table, []).append(field_name)
    return {table: list(dict.fromkeys(values)) for table, values in fields.items() if values}

def _extract_custom_routes(text: str, resources: list[str], known_tables: set[str]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for match in re.finditer(r"top\s+(\d+)\s+([a-z0-9_]+)\s+that\s+have\s+([a-z0-9_]+)", text):
        limit = int(match.group(1))
        parent = _resolve_table_name(match.group(2), known_tables)
        child = _resolve_table_name(match.group(3), known_tables)
        parent_id = f"{_singular(parent)}_id"
        routes.append(
            {
                "kind": "top_related",
                "method": "GET",
                "path": f"/{parent}/top-by-{child}",
                "parent_table": parent,
                "child_table": child,
                "limit": limit,
                "required_tables": [{"table": parent}, {"table": child}],
                "required_fields": [
                    {"table": child, "field": parent_id, "references": f"{parent}(id)"}
                ],
                "required_relationships": [
                    {"from_table": child, "field": parent_id, "to_table": parent}
                ],
                "required_behaviors": [{"behavior": "top_n", "table": parent}],
            }
        )
    page_size = 10
    page_match = re.search(r"(?:paginate|pagination)[^\d]*(\d+)", text)
    if page_match:
        page_size = int(page_match.group(1))
    for match in re.finditer(r"paginate\s+(?:the\s+)?([a-z0-9_]+).*?(?:for|per)\s+each\s+([a-z0-9_]+)", text):
        child = _resolve_table_name(match.group(1), known_tables)
        parent = _resolve_table_name(match.group(2), known_tables)
        parent_id = f"{_singular(parent)}_id"
        routes.append(
            {
                "kind": "parent_children_paginated",
                "method": "GET",
                "path": f"/{parent}/{{{parent_id}}}/{child}",
                "parent_table": parent,
                "child_table": child,
                "page_size": page_size,
                "required_tables": [{"table": parent}, {"table": child}],
                "required_filters": [{"table": child, "field": parent_id, "references": f"{parent}(id)"}],
                "required_relationships": [
                    {"from_table": child, "field": parent_id, "to_table": parent}
                ],
                "required_behaviors": [{"behavior": "pagination", "table": child}],
            }
        )
    for match in re.finditer(r"(?:grouped by|groups? .* by)\s+([a-z0-9_]+)", text):
        field = _identifier(match.group(1))
        table = resources[0] if resources else ""
        if table and field:
            routes.append(
                {
                    "kind": "aggregate_count_by_field",
                    "method": "GET",
                    "path": f"/analytics/{_singular(table)}-{field}-summary",
                    "table": table,
                    "field": field,
                    "required_fields": [{"table": table, "field": field}],
                    "required_behaviors": [{"behavior": "aggregate", "table": table}],
                }
            )
    return routes


def _extract_explicit_routes(text: str) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for match in re.finditer(
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s,;]+)",
        text,
        flags=re.IGNORECASE,
    ):
        path = match.group(2).rstrip(".:)")
        routes.append(
            {
                "kind": "explicit",
                "method": match.group(1).upper(),
                "path": path,
            }
        )
    return routes


def _extract_access_rules(
    text: str,
    explicit_routes: list[dict[str, Any]],
    known_tables: set[str],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    action_methods = {
        "create": "POST",
        "list": "GET",
        "detail": "GET",
        "update": "PUT",
        "delete": "DELETE",
    }
    for match in re.finditer(
        r"(?:make\s+)?(create|list|detail|update|delete)\s+([a-z0-9_]+)\s+admin\s+only",
        text,
    ):
        action, resource = match.groups()
        table = _resolve_table_name(resource, known_tables)
        method = action_methods[action]
        base = f"/{_plural(table)}"
        path = base if action in {"create", "list"} else f"{base}/{{{_singular(table)}_id}}"
        rules.append(
            {
                "behavior": "role_restricted",
                "table": table,
                "method": method,
                "path": path,
                "role": "admin",
            }
        )
    for match in re.finditer(
        r"\b(teachers?|students?|admins?|users?)\s+can\s+"
        r"(create|list|detail|update|delete)\s+((?:their\s+own\s+)?[a-z0-9_]+)",
        text,
    ):
        raw_role, action, raw_resource = match.groups()
        role = _singular(raw_role)
        resource = re.sub(r"^their\s+own\s+", "", raw_resource)
        table = _resolve_table_name(resource, known_tables)
        method = action_methods[action]
        base = f"/{_plural(table)}"
        path = base if action in {"create", "list"} else f"{base}/{{{_singular(table)}_id}}"
        rules.append(
            {
                "behavior": "role_restricted",
                "table": table,
                "method": method,
                "path": path,
                "role": role,
            }
        )
        clause_end = text.find(".", match.end())
        clause = text[match.start(): clause_end if clause_end >= 0 else len(text)]
        if "their own" in raw_resource or " for their " in clause:
            rules.append(
                {
                    "behavior": "current_user_scoped",
                    "table": table,
                    "method": method,
                    "path": path,
                }
            )
    if re.search(r"\bcurrent\s+[a-z0-9_]+(?:'s|\s+s)?\b", text):
        for route in explicit_routes:
            rules.append(
                {
                    "behavior": "current_user_scoped",
                    "method": route.get("method") or "GET",
                    "path": route.get("path") or "",
                }
            )
    return rules




def _extract_book_commerce_routes(text: str, known_tables: set[str]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    book_table = _resolve_table_name("books", known_tables | {"books"})
    if re.search(r"\b(?:most|top|best)\s+sold\s+books?\b|\bbooks?\s+.*\b(?:most|top|best)\s+sold\b", text):
        sales_table = "book_sales"
        routes.append(
            {
                "kind": "top_related",
                "method": "GET",
                "path": f"/{book_table}/top-by-sales",
                "parent_table": book_table,
                "child_table": sales_table,
                "limit": 10,
                "required_tables": [{"table": book_table}, {"table": sales_table}],
                "required_fields": [
                    {"table": sales_table, "field": "book_id", "references": f"{book_table}(id)"},
                    {"table": sales_table, "field": "quantity"},
                    {"table": sales_table, "field": "sold_at"},
                ],
                "required_relationships": [
                    {"from_table": sales_table, "field": "book_id", "to_table": book_table}
                ],
                "required_behaviors": [{"behavior": "top_n", "table": book_table}],
            }
        )
    if "discount" in text or "offer" in text or "offers" in text:
        discount_table = "discounts"
        routes.append(
            {
                "kind": "parent_children_paginated",
                "method": "GET",
                "path": f"/{book_table}/{{book_id}}/{discount_table}",
                "parent_table": book_table,
                "child_table": discount_table,
                "page_size": 10,
                "required_tables": [{"table": book_table}, {"table": discount_table}],
                "required_filters": [
                    {"table": discount_table, "field": "book_id", "references": f"{book_table}(id)"}
                ],
                "required_fields": [
                    {"table": discount_table, "field": "title"},
                    {"table": discount_table, "field": "description"},
                    {"table": discount_table, "field": "percent"},
                    {"table": discount_table, "field": "active"},
                ],
                "required_relationships": [
                    {"from_table": discount_table, "field": "book_id", "to_table": book_table}
                ],
                "required_behaviors": [{"behavior": "pagination", "table": discount_table}],
            }
        )
    return routes
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


def _append_crud_routes(contract: dict[str, list[dict[str, Any]]], resource: str, actions: set[str]) -> None:
    base = f"/{_plural(resource)}"
    item = f"{base}/{{{_singular(resource)}_id}}"
    route_by_action = {
        "create": ("POST", base),
        "list": ("GET", base),
        "detail": ("GET", item),
        "update": ("PUT", item),
        "delete": ("DELETE", item),
    }
    for action in ("create", "list", "detail", "update", "delete"):
        if action not in actions:
            continue
        method, path = route_by_action[action]
        _append_unique(contract, "required_routes", {"method": method, "path": path}, ("method", "path"))


def _add_schema_field(
    schema_additions: dict[str, list[dict[str, Any]]],
    table: str,
    field: str,
    *,
    field_type: str = "str",
    filterable: bool = False,
    references: Any = None,
) -> None:
    table = _plural(table)
    field = _identifier(field)
    if not table or not _usable_field(field):
        return
    tables = schema_additions.setdefault("tables", [])
    found = next((item for item in tables if _table_key(item) == table), None)
    if found is None:
        found = {"name": table, "columns": []}
        tables.append(found)
    column: dict[str, Any] = {"name": field, "type": field_type}
    if filterable:
        column["filter"] = True
        column["index"] = True
    if references:
        column["references"] = references
    _append_unique_to_list(found.setdefault("columns", []), column, ("name",))


def _merge_columns(table: dict[str, Any], columns: list[dict[str, Any]]) -> None:
    existing = [column for column in table.get("columns") or table.get("fields") or [] if isinstance(column, dict)]
    for column in columns:
        if not isinstance(column, dict):
            continue
        name = _identifier(str(column.get("name") or ""))
        current = next(
            (
                item
                for item in existing
                if _identifier(str(item.get("name") or "")) == name
            ),
            None,
        )
        if current is None:
            existing.append(dict(column))
            continue
        incoming = {
            key: value
            for key, value in column.items()
            if value not in (None, "", [])
        }
        if current.get("type") and str(incoming.get("type") or "").upper() == "TEXT":
            incoming.pop("type", None)
        current.update(incoming)
    table["columns"] = existing


def _append_unique(target: dict[str, list[dict[str, Any]]], key: str, item: dict[str, Any], keys: tuple[str, ...]) -> None:
    values = target.setdefault(key, [])
    _append_unique_to_list(values, item, keys)


def _append_unique_to_list(values: list[dict[str, Any]], item: dict[str, Any], keys: tuple[str, ...]) -> None:
    marker = tuple(str(item.get(key) or "").strip().lower() for key in keys)
    for index, existing in enumerate(values):
        if tuple(str(existing.get(key) or "").strip().lower() for key in keys) == marker:
            values[index] = {
                **existing,
                **{key: value for key, value in item.items() if value not in (None, "", [])},
            }
            return
    values.append({key: value for key, value in item.items() if value not in (None, "", [])})


def _marker_keys(key: str) -> tuple[str, ...]:
    if key == "required_routes":
        return ("method", "path")
    if key in {"required_fields", "required_filters"}:
        return ("table", "field")
    if key == "required_relationships":
        return ("from_table", "field", "to_table")
    if key == "required_behaviors":
        return ("behavior", "table", "method", "path", "role")
    return ("table",)


def _empty_contract() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in ARTIFACT_KEYS}


def _has_public_resource_intent(text: str) -> bool:
    return any(term in text for term in ("can be public", "resource endpoints can be public", "public endpoint", "public endpoints"))


def _has_auth_intent(text: str) -> bool:
    return any(term in text for term in ("auth", "jwt", "protected", "protect ", "admin-only", "admin only"))


def _schema_tables(schema: dict[str, Any] | None) -> set[str]:
    if not schema:
        return set()
    nested_schema = schema.get("schema")
    raw_schema = nested_schema if isinstance(nested_schema, dict) else schema
    return {
        _plural(str(table.get("name") or table.get("table") or ""))
        for table in raw_schema.get("tables") or []
        if isinstance(table, dict) and (table.get("name") or table.get("table"))
    }


def _resolve_table_name(value: str, known_tables: set[str]) -> str:
    table = _plural(value)
    if table in known_tables:
        return table
    candidates = [
        candidate
        for candidate in sorted(known_tables)
        if candidate[:1] == table[:1] and abs(len(candidate) - len(table)) <= 2
    ]
    matches = get_close_matches(table, candidates, n=1, cutoff=0.72)
    return matches[0] if matches else table


def _field_type_from_name(field: str, *, context: str = "") -> str:
    field = _identifier(field)
    phrase = re.escape(field).replace(r"\_", r"[_\s]+")
    type_patterns = {
        "BOOLEAN": r"boolean|bool",
        "INTEGER": r"integer|int",
        "float": r"float|decimal|number",
        "TIMESTAMP": r"datetime|timestamp|date",
        "TEXT": r"string|text",
    }
    for field_type, type_pattern in type_patterns.items():
        if re.search(
            rf"(?:{type_pattern})\s+(?:field\s+)?{phrase}\b|\b{phrase}\s+(?:{type_pattern})\b",
            context,
        ):
            return field_type
    if any(term in field for term in ("price", "amount", "cost", "total", "score", "rate", "grade")):
        return "float"
    if field.startswith("is_") or field in {"paid", "done", "enabled", "approved", "acknowledged", "active", "pinned"}:
        return "BOOLEAN"
    if field.endswith("_id") or field in {"count", "quantity", "limit", "page"}:
        return "INTEGER"
    if "date" in field or field.endswith("_at"):
        return "TIMESTAMP"
    return "TEXT"


def _reference_table_for_field(field: str, known_tables: set[str]) -> str | None:
    normalized = _identifier(field)
    if not normalized.endswith("_id"):
        return None
    candidate = _plural(normalized[:-3])
    return candidate if candidate in known_tables else None


def _split_list(value: str) -> list[str]:
    cleaned = re.sub(r"\band\b", ",", value)
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _normalize_text(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt.lower().replace("-", " ")).strip()


def _identifier(value: str) -> str:
    return _normalized_identifier(value)


def _field_identifier(value: str) -> str:
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", str(value or "").strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:field|fields|column|columns|attr|attrs|attribute|attributes)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:boolean|bool|string|text|integer|int|float|decimal|datetime|timestamp)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    field = _identifier(cleaned)
    return "role" if field == "roles" else field


def _usable_field(field: str) -> bool:
    return bool(
        field
        and field not in _STOP_FIELD_WORDS
        and not field[0].isdigit()
        and not field.endswith("_table")
        and not field.endswith("_api")
    )


def _singular(value: str) -> str:
    return _normalized_singular(value)


def _plural(value: str) -> str:
    return _normalized_plural(value)


def _table_key(table: dict[str, Any]) -> str:
    return _plural(str(table.get("name") or table.get("table") or table.get("model") or ""))


# Determiners are never resource names; when a prompt says "the tasks table"
# the noun after the determiner is the resource.
_DETERMINERS = ("the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "my", "your")
_DETERMINER_PREFIX = rf"(?:(?:{'|'.join(_DETERMINERS)})\s+)?"
_STOP_RESOURCE_WORDS = {
    *_DETERMINERS,
    *(_plural(word) for word in _DETERMINERS),
    "api",
    "apis",
    "app",
    "backend",
    "project",
    "service",
    "system",
    "auth",
    "jwt",
    "websocket",
    "notifications",
    "return",
    "returns",
    "create",
    "list",
    "detail",
    "update",
    "filters",
    "filter",
    "actions",
    "endpoint",
    "endpoints",
    "resource",
    "resources",
}
_STOP_FIELD_WORDS = {
    "create",
    "list",
    "detail",
    "update",
    "actions",
    "api",
    "apis",
    "endpoint",
    "endpoints",
    "for",
    "each",
    "pagination",
    "paginate",
    "it",
}
