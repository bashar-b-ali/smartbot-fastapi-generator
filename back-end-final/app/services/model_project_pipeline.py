from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.exceptions import ValidationError
from app.llm.file_spec import FileSpec
from app.llm.parsers import ResponseParser
from app.services.model_pipeline.intent import extract_prompt_intent
from app.services.model_pipeline.llm_io import (
    extract_json_object,
)
from app.services.model_pipeline.paths import (
    assert_not_placeholder as _assert_not_placeholder,
)
from app.services.model_pipeline.paths import (
    canonical_project_path as _canonical_project_path,
)
from app.services.model_pipeline.paths import (
    is_safe_file_path as _is_safe_file_path,
)
from app.services.model_pipeline.paths import (
    looks_like_requirements_content as _looks_like_requirements_content,
)
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.project_edit.artifact_validation import (
    normalize_requirement_contract,
    validate_artifacts,
)
from app.services.project_edit.io import (
    read_project_file,
)

MAX_EDIT_FILE_CONTEXT_CHARS = 18_000
MAX_PATCH_FILE_CONTEXT_CHARS = 7_000
MAX_FULL_FILE_EDIT_CONTEXT_CHARS = 12_000
ARTIFACT_KEYS = (
    "required_routes",
    "required_tables",
    "required_fields",
    "required_filters",
    "required_relationships",
    "required_behaviors",
    "removed_artifacts",
)


def _has_artifact_checks(contract: dict[str, Any] | None) -> bool:
    return bool(contract and any(contract.get(key) for key in ARTIFACT_KEYS))


def _looks_like_dependency_requirements(raw: Any) -> bool:
    if not isinstance(raw, list) or not raw:
        return False
    dict_items = [item for item in raw if isinstance(item, dict)]
    if len(dict_items) != len(raw):
        return False
    dependency_kinds = {"dependency", "dependencies", "library", "package", "packages", "framework", "tool"}
    dependency_terms = {
        "fastapi",
        "sqlalchemy",
        "sqlmodel",
        "flask",
        "flask-sqlalchemy",
        "flask-jwt-extended",
        "uvicorn",
        "pydantic",
        "jwt",
        "redis",
        "websocket",
    }
    behavior_terms = {
        "endpoint",
        "route",
        "create",
        "list",
        "detail",
        "update",
        "delete",
        "filter",
        "auth",
        "table",
        "field",
        "relationship",
    }
    if all(str(item.get("kind") or "").strip().lower() in dependency_kinds for item in dict_items):
        return True
    dependency_like = 0
    for item in dict_items:
        kind = str(item.get("kind") or item.get("type") or "").strip().lower()
        description = " ".join(str(item.get(key) or "") for key in ("description", "name", "title")).lower()
        if kind in dependency_kinds:
            dependency_like += 1
            continue
        if any(term in description for term in dependency_terms) and not any(term in description for term in behavior_terms):
            dependency_like += 1
    if dependency_like == len(dict_items):
        return True
    return dependency_like >= max(2, len(dict_items) // 2 + 1)


def _compact_param(param: dict[str, Any]) -> dict[str, Any]:
    item = {
        "name": param.get("name"),
        "in": param.get("in"),
        "type": param.get("type"),
        "required": bool(param.get("required")),
    }
    if "default" in param:
        item["default"] = param.get("default")
    return item


def _compact_table(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": table.get("name"),
        "file": table.get("file"),
        "db_table": table.get("db_table_name") or None,
        "line": table.get("line"),
        "kind": table.get("kind"),
        "fields": [
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "line": field.get("line"),
                "foreign_key": field.get("foreign_key"),
                "primary_key": bool(field.get("primary_key")),
                "index": bool(field.get("index")),
                "nullable": field.get("nullable"),
                "default": field.get("default"),
            }
            for field in (table.get("fields") or [])[:32]
        ],
    }


def _compact_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": route.get("method"),
        "path": route.get("path"),
        "file": route.get("file"),
        "function": route.get("function"),
        "line": route.get("line"),
        "parameters": [_compact_param(param) for param in (route.get("parameters") or [])[:16]],
        "response_model": route.get("response_model") or None,
        "body_schema_names": (route.get("body_schema_names") or [])[:8] or None,
        "status_code": route.get("status_code"),
    }


def _compact_class(class_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": class_info.get("name"),
        "file": class_info.get("file"),
        "line": class_info.get("line"),
        "bases": (class_info.get("bases") or [])[:8],
        "db_table": class_info.get("db_table_name") or None,
        "is_sqlmodel_table": bool(class_info.get("is_sqlmodel_table")),
        "is_sqlalchemy_table": bool(class_info.get("is_sqlalchemy_table")),
        "is_pydantic_model": bool(class_info.get("is_pydantic_model")),
        "fields": [
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "line": field.get("line"),
                "foreign_key": field.get("foreign_key"),
                "primary_key": bool(field.get("primary_key")),
                "index": bool(field.get("index")),
                "nullable": field.get("nullable"),
            }
            for field in (class_info.get("fields") or [])[:32]
        ],
    }


def _compact_function(fn: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": fn.get("name"),
        "file": fn.get("file"),
        "signature": fn.get("signature"),
        "line": fn.get("line"),
        "is_async": bool(fn.get("is_async")),
        "route": _compact_route(fn.get("route") or {}) if fn.get("route") else None,
        "summary": fn.get("summary"),
        "body_symbols": (fn.get("body_symbols") or [])[:48],
        "query_filters": (fn.get("query_filters") or [])[:16],
    }


def _compact_project_index(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "stats": index.get("stats") or {},
        "files": (index.get("file_index") or [])[:120],
        "tables": [_compact_table(table) for table in (index.get("database_schema") or {}).get("tables") or []][:60],
        "classes": [_compact_class(item) for item in index.get("class_summaries") or []][:80],
        "routes": [_compact_route(route) for route in index.get("api_routes") or []][:120],
        "functions": [_compact_function(fn) for fn in index.get("function_summaries") or []][:120],
        "query_filters": (index.get("query_filters") or [])[:120],
        "router_wiring": (index.get("router_wiring") or [])[:80],
    }


def _compact_edit_index(index: dict[str, Any], selected_files: list[str]) -> dict[str, Any]:
    selected = set(selected_files)
    compact = _compact_project_index(index)

    def selected_file(item: dict[str, Any]) -> bool:
        return not selected or str(item.get("file") or item.get("path") or "") in selected

    compact["files"] = [item for item in compact.get("files") or [] if selected_file(item) or item.get("parse_error")][:40]
    compact["tables"] = [item for item in compact.get("tables") or [] if selected_file(item)][:32]
    compact["classes"] = [item for item in compact.get("classes") or [] if selected_file(item)][:48]
    compact["routes"] = [item for item in compact.get("routes") or [] if selected_file(item)][:64]
    compact["functions"] = [item for item in compact.get("functions") or [] if selected_file(item)][:64]
    compact["query_filters"] = [item for item in compact.get("query_filters") or [] if selected_file(item)][:64]
    compact["router_wiring"] = [
        item
        for item in compact.get("router_wiring") or []
        if (
            not selected
            or str(item.get("file") or "") in selected
            or str(item.get("router_file") or "") in selected
        )
    ][:40]
    return compact

def _compact_code_context(index: dict[str, Any], selected: list[str]) -> dict[str, Any]:
    """Minimal project context for stages that must write code.

    The full edit index costs several times the prompt budget of the source it
    describes, so it crowds out the file contents the model has to patch. Code
    stages only need to know which routes/tables already exist and where they
    live; the source files carry the rest.
    """
    chosen = set(selected)

    def in_scope(item: dict[str, Any]) -> bool:
        return not chosen or str(item.get("file") or "") in chosen

    return {
        "stats": index.get("stats") or {},
        "selected_files": list(selected),
        "existing_routes": [
            f"{route.get('method')} {route.get('path')} -> {route.get('file')}:{route.get('function')}"
            for route in (index.get("api_routes") or [])[:80]
            if route.get("path")
        ],
        "existing_tables": [
            {
                "name": table.get("name"),
                "file": table.get("file"),
                "fields": [field.get("name") for field in (table.get("fields") or [])[:32] if field.get("name")],
            }
            for table in ((index.get("database_schema") or {}).get("tables") or [])[:24]
        ],
        "classes_in_scope": [
            {"name": item.get("name"), "file": item.get("file")}
            for item in (index.get("class_summaries") or [])
            if in_scope(item)
        ][:40],
        "router_wiring": [
            {"file": item.get("file"), "router_file": item.get("router_file")}
            for item in (index.get("router_wiring") or [])[:24]
        ],
    }


def _normalize_file_specs(raw_files: list[dict[str, Any]]) -> list[FileSpec]:
    specs: list[FileSpec] = []
    seen: set[str] = set()
    for item in raw_files:
        path = _canonical_project_path(str(item.get("path") or item.get("file") or item.get("filename") or ""))
        if not _is_safe_file_path(path):
            raise ValidationError(f"Unsafe or unsupported generated path: {path}")
        _assert_not_placeholder(path, label="file path")
        content = item.get("content") if "content" in item else item.get("code")
        if not isinstance(content, str) or not content.strip():
            raise ValidationError(f"Generated file `{path}` has empty content")
        content = _strip_markdown_file_fence(content)
        _assert_not_placeholder(content, label=f"file `{path}`")
        if path not in seen:
            specs.append(FileSpec(path=path, content=content.replace("\r\n", "\n")))
            seen.add(path)
    return specs


def _strip_markdown_file_fence(content: str) -> str:
    stripped = content.strip()
    match = re.fullmatch(r"```(?:[A-Za-z0-9_+-]+)?\s*\n(?P<body>.*?)\n?```", stripped, flags=re.DOTALL)
    if match:
        return match.group("body").strip()
    stripped = re.sub(r"^```(?:[A-Za-z0-9_+-]+)?\s*\n", "", stripped)
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped


def _parse_marked_file_specs(text: str) -> list[FileSpec]:
    parsed = ResponseParser.parse_code_generation(text)
    if not parsed.files:
        try:
            data = extract_json_object(text)
        except ValidationError:
            data = {}
        raw_json_files = data.get("files") if isinstance(data, dict) else None
        if isinstance(raw_json_files, list):
            return _normalize_file_specs([item for item in raw_json_files if isinstance(item, dict)])
        if isinstance(raw_json_files, dict):
            return _normalize_file_specs(
                [{"path": str(path), "content": content} for path, content in raw_json_files.items()]
            )
    raw_files = [
        {"path": item.filename.replace("\\", "/"), "content": item.content}
        for item in parsed.files
    ]
    return _normalize_file_specs(raw_files)


def _normalize_required_file_names(files: list[FileSpec]) -> list[FileSpec]:
    files = [FileSpec(path=_canonical_project_path(item.path), content=item.content) for item in files]
    paths = {item.path for item in files}
    normalized: list[FileSpec] = []
    used_replacements: set[str] = set()
    for item in files:
        target = item.path
        if item.path.startswith("code_") or item.path in {"output.txt", "response.txt"}:
            content = item.content
            if (
                FASTAPI_PROFILE.entrypoint_file not in paths
                and FASTAPI_PROFILE.entrypoint_file not in used_replacements
                and "FastAPI(" in content
            ):
                target = FASTAPI_PROFILE.entrypoint_file
            elif (
                FASTAPI_PROFILE.database_file not in paths
                and FASTAPI_PROFILE.database_file not in used_replacements
                and ("create_engine(" in content or "SQLModel.metadata.create_all" in content or "def get_session" in content)
            ):
                target = FASTAPI_PROFILE.database_file
            elif (
                FASTAPI_PROFILE.dependency_file not in paths
                and FASTAPI_PROFILE.dependency_file not in used_replacements
                and _looks_like_requirements_content(content)
            ):
                target = FASTAPI_PROFILE.dependency_file
        used_replacements.add(target)
        normalized.append(FileSpec(path=target, content=item.content))
    deduped: list[FileSpec] = []
    seen: set[str] = set()
    for item in normalized:
        if item.path in seen:
            continue
        deduped.append(item)
        seen.add(item.path)
    return deduped


def _requirements(data: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    raw = data.get("requirements") or data.get("acceptance_requirements") or data.get("features")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("model contract must include non-empty requirements")
    requirements: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            continue
        description = str(
            item.get("description")
            or item.get("requirement")
            or item.get("title")
            or item.get("name")
            or ""
        ).strip()
        if not description:
            continue
        requirements.append(
            {
                "id": str(item.get("id") or f"R{idx}"),
                "kind": str(item.get("kind") or "other"),
                "description": description,
                "entities": [str(v) for v in item.get("entities") or [] if str(v).strip()],
                "acceptance_checks": [
                    str(v) for v in item.get("acceptance_checks") or item.get("checks") or [] if str(v).strip()
                ],
                "critical": bool(item.get("critical", True)),
            }
        )
    if not requirements:
        raise ValidationError(f"model contract did not produce usable requirements for: {prompt[:120]}")
    return requirements


ARTIFACT_ALIAS_KEYS = (
    "routes",
    "endpoints",
    "tables",
    "models",
    "fields",
    "filters",
    "relationships",
    "behaviors",
    "removed",
)


def _merge_artifact_contracts(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not primary:
        return fallback
    if not fallback:
        return primary
    merged = dict(primary)
    for key in ARTIFACT_KEYS:
        values = list(merged.get(key) or [])
        for item in fallback.get(key) or []:
            if item not in values:
                values.append(item)
        if values:
            merged[key] = values
    return merged


def _artifact_contract_from_data(data: dict[str, Any]) -> dict[str, Any] | None:
    nested = _coerce_artifact_contract(data.get("artifact_contract"))
    top_level_source = {
        key: data[key]
        for key in (*ARTIFACT_KEYS, *ARTIFACT_ALIAS_KEYS)
        if isinstance(data.get(key), (list, dict))
    }
    top_level = _coerce_artifact_contract(top_level_source) if top_level_source else None
    if not _has_artifact_checks(nested):
        return top_level or nested
    if not _has_artifact_checks(top_level):
        return nested
    return _merge_artifact_contracts(nested, top_level)


def _finalize_contract_data(data: dict[str, Any], *, prompt: str, source: str) -> dict[str, Any]:
    finalized = dict(data)
    raw_contract = _artifact_contract_from_data(finalized)
    prompt_contract = _artifact_contract_from_prompt(prompt)
    try:
        requirements = _requirements(finalized, prompt)
    except ValidationError:
        requirements = _requirements_from_model_artifacts(raw_contract)
    if _looks_like_dependency_requirements(requirements):
        requirements = _requirements_from_model_artifacts(raw_contract)
    if not requirements:
        raw_contract = _merge_artifact_contracts(raw_contract, prompt_contract)
        if raw_contract:
            finalized["artifact_contract"] = raw_contract
        requirements = _requirements_from_model_artifacts(raw_contract)
    if not requirements:
        raise ValidationError(f"model contract did not produce usable requirements for: {prompt[:120]}")
    finalized["requirements"] = requirements
    artifact_contract = _contract_from_data(
        finalized,
        prompt=prompt,
        requirements=requirements,
        source=source,
    )
    finalized["artifact_contract"] = artifact_contract
    finalized["requirements"] = artifact_contract.get("requirements") or requirements
    return finalized


def _artifact_contract_from_prompt(prompt: str) -> dict[str, Any] | None:
    intent = extract_prompt_intent(prompt)
    contract = intent.get("contract") if isinstance(intent.get("contract"), dict) else {}
    return _coerce_artifact_contract(contract) if contract else None


def _requirements_from_model_artifacts(raw_contract: dict[str, Any] | None) -> list[dict[str, Any]]:
    contract = _coerce_artifact_contract(raw_contract) if raw_contract is not None else None
    if not _has_artifact_checks(contract):
        return []
    pieces: list[str] = []
    if contract.get("required_tables"):
        pieces.append("tables")
    if contract.get("required_fields"):
        pieces.append("fields")
    if contract.get("required_routes"):
        pieces.append("routes")
    if contract.get("required_filters"):
        pieces.append("filters")
    if contract.get("required_relationships"):
        pieces.append("relationships")
    if contract.get("required_behaviors"):
        pieces.append("behaviors")
    return [
        {
            "id": "R1",
            "kind": "artifact_contract",
            "description": "Implement the model-supplied artifact contract"
            + (f" covering {', '.join(pieces)}." if pieces else "."),
            "entities": [],
            "acceptance_checks": ["All explicit artifact checks pass."],
            "critical": True,
        }
    ]



SCHEMA_META_KEYS = {
    "relationships",
    "indexes",
    "filters",
    "search_fields",
    "filters/search_fields",
    "filter_fields",
    "no_database_required",
}


def _is_schema_meta_table(table: Any) -> bool:
    if not isinstance(table, dict):
        return True
    name = str(table.get("name") or table.get("table") or "").strip().lower()
    return not name or name in SCHEMA_META_KEYS


def _normalize_schema_plan_shape(plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(plan)
    ddl_sql = normalized.get("ddl_sql")
    if isinstance(ddl_sql, list):
        normalized["ddl_sql"] = "\n".join(str(item) for item in ddl_sql if str(item).strip())
    elif ddl_sql is None:
        normalized["ddl_sql"] = ""

    schema = normalized.get("schema") if isinstance(normalized.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else None
    if tables is not None:
        # Models sometimes emit `search_fields`/`filters` as if they were tables;
        # rendering those produces a bogus SQLModel table and router.
        kept = [table for table in tables if not _is_schema_meta_table(table)]
        if len(kept) != len(tables):
            tables = kept
            normalized["schema"] = {**schema, "tables": tables}
    if tables is None:
        tables = []
        for table_name, raw_columns in schema.items():
            if str(table_name).strip().lower() in SCHEMA_META_KEYS or not isinstance(raw_columns, dict):
                continue
            columns = []
            for column_name, raw_type in raw_columns.items():
                if isinstance(raw_type, dict):
                    column = {"name": column_name, **raw_type}
                else:
                    column = {"name": column_name, "type": str(raw_type)}
                columns.append(column)
            tables.append({"name": table_name, "columns": columns})
        schema = {**schema, "tables": tables}
        normalized["schema"] = schema

    if not normalized.get("affected_tables") and tables:
        normalized["affected_tables"] = [
            table.get("name") or table.get("table")
            for table in tables
            if isinstance(table, dict) and (table.get("name") or table.get("table"))
        ]
    if not normalized.get("affected_fields") and tables:
        fields = []
        for table in tables:
            if not isinstance(table, dict):
                continue
            table_name = table.get("name") or table.get("table") or ""
            for column in table.get("columns") or table.get("fields") or []:
                if isinstance(column, dict) and column.get("name"):
                    fields.append(f"{table_name}.{column['name']}" if table_name else column["name"])
        normalized["affected_fields"] = fields
    return normalized

def _validate_schema_plan(plan: dict[str, Any]) -> dict[str, Any]:
    plan.update(_normalize_schema_plan_shape(plan))
    errors: list[str] = []
    warnings: list[str] = []
    ddl = str(plan.get("ddl_sql") or "").strip()
    schema = plan.get("schema") if isinstance(plan.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    no_database_required = bool(plan.get("no_database_required") or schema.get("no_database_required"))
    table_names = {
        str(table.get("name") or table.get("table") or "").strip().lower()
        for table in tables
        if isinstance(table, dict)
    }
    table_names.discard("")
    ddl_tables = {
        match.group(1).strip('"`[]').lower()
        for match in re.finditer(r"(?is)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([a-zA-Z_][\w]*)", ddl)
    }
    if no_database_required:
        return {"passed": True, "errors": [], "warnings": warnings, "tables": []}
    if not ddl:
        errors.append("Schema stage did not return ddl_sql.")
    if not table_names:
        errors.append("Schema stage did not return structured schema.tables.")
    if ddl and not ddl_tables:
        warnings.append("ddl_sql did not contain recognizable CREATE TABLE statements.")
    missing_in_ddl = sorted(table_names - ddl_tables) if ddl_tables else []
    if missing_in_ddl:
        errors.append("Structured schema tables missing from ddl_sql: " + ", ".join(missing_in_ddl[:12]))
    known_tables = table_names | ddl_tables
    for table in tables:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or table.get("table") or "").strip()
        columns = table.get("columns") or table.get("fields") or []
        if not columns:
            errors.append(f"Schema table `{name or '<unnamed>'}` has no columns.")
        for rel in table.get("relationships") or table.get("foreign_keys") or []:
            if not isinstance(rel, dict):
                continue
            target = str(rel.get("references_table") or rel.get("target_table") or rel.get("table") or "").lower()
            if target and target not in known_tables:
                errors.append(f"Schema relationship from `{name}` references missing table `{target}`.")
    return {"passed": not errors, "errors": errors, "warnings": warnings, "tables": sorted(known_tables)}


def _contract_from_data(
    data: dict[str, Any],
    *,
    prompt: str,
    requirements: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    raw_contract = _artifact_contract_from_data(data)
    normalized = normalize_requirement_contract(
        raw_contract,
        prompt=prompt,
        requirements=requirements,
        source=source,
    )
    if not any(normalized.get(key) for key in ARTIFACT_KEYS):
        raise ValidationError("model contract must include explicit artifact checks")
    return normalized


def _empty_artifact_contract() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in ARTIFACT_KEYS}


def _coerce_artifact_contract(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        contract = dict(raw)
        alias_map = {
            "routes": "required_routes",
            "endpoints": "required_routes",
            "tables": "required_tables",
            "models": "required_tables",
            "fields": "required_fields",
            "filters": "required_filters",
            "filters/search_fields": "required_filters",
            "search_fields": "required_filters",
            "relationships": "required_relationships",
            "behaviors": "required_behaviors",
            "removed": "removed_artifacts",
        }
        for alias, canonical in alias_map.items():
            if canonical not in contract and isinstance(contract.get(alias), list):
                contract[canonical] = _coerce_artifact_items(canonical, contract[alias])
        if isinstance(raw.get("fields"), dict):
            contract["required_fields"] = [
                *contract.get("required_fields", []),
                *_coerce_field_map(raw["fields"], kind="required_fields"),
            ]
        if isinstance(raw.get("filters"), dict):
            contract["required_filters"] = [
                *contract.get("required_filters", []),
                *_coerce_field_map(raw["filters"], kind="required_filters"),
            ]
        for key, value in raw.items():
            if key in ARTIFACT_KEYS or key in alias_map or key in CONTRACT_METADATA_KEYS:
                continue
            if key.lower() in {"endpoints", "api_endpoints"} and isinstance(value, list):
                contract.setdefault("required_routes", []).extend(_coerce_artifact_items("required_routes", value))
            elif key.lower() in {"fields", "columns", "properties"} and isinstance(value, dict):
                contract.setdefault("required_fields", []).extend(_coerce_field_map(value, kind="required_fields"))
            elif key.lower() in {"filters", "query_filters"} and isinstance(value, dict):
                contract.setdefault("required_filters", []).extend(_coerce_field_map(value, kind="required_filters"))
            elif key.lower() in {"entities", "resources"} and isinstance(value, list):
                _append_model_property_artifacts(contract, value)
        return contract
    if not isinstance(raw, list):
        return None
    contract = _empty_artifact_contract()
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("method") and item.get("path"):
            contract["required_routes"].append(item)
            continue
        if item.get("from_table") or item.get("to_table"):
            contract["required_relationships"].append(item)
            continue
        if item.get("behavior") or item.get("kind") == "behavior":
            contract["required_behaviors"].append(item)
            continue
        if item.get("table") and item.get("field"):
            contract["required_fields"].append(item)
            continue
        if item.get("table") or item.get("model") or item.get("name"):
            contract["required_tables"].append(item)
    return contract


CONTRACT_METADATA_KEYS = {
    "source",
    "prompt",
    "requirements",
    "summary",
    "description",
    "assumptions",
}


def _coerce_artifact_items(kind: str, values: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            if kind == "required_routes":
                method = value.get("method") or value.get("http_method") or "*"
                path = value.get("path") or value.get("route") or value.get("url")
                if path:
                    methods = method if isinstance(method, list) else [method]
                    for single_method in methods:
                        items.append({"method": str(single_method).upper(), "path": str(path)})
            elif kind in {"required_fields", "required_filters"}:
                table = value.get("table") or value.get("model") or value.get("class")
                field = value.get("field") or value.get("column") or value.get("name")
                if table and field:
                    items.append({"table": table, "field": field})
                elif kind == "required_fields":
                    items.append(value)
            elif kind == "required_relationships":
                table = value.get("from_table") or value.get("table")
                field = value.get("field") or value.get("column")
                columns = value.get("columns")
                if not field and isinstance(columns, list) and columns:
                    field = columns[0]
                target = (
                    value.get("to_table")
                    or value.get("target_table")
                    or value.get("related_table")
                    or value.get("references_table")
                    or value.get("to")
                )
                if table and target:
                    items.append({"from_table": table, "field": field or "", "to_table": target})
                else:
                    items.append(value)
            else:
                items.append(value)
        elif isinstance(value, str):
            if kind == "required_routes":
                items.append({"path": value, "method": "*"})
            elif kind == "required_tables":
                items.append({"table": value})
            elif kind in {"required_fields", "required_filters"} and "." in value:
                table, field = value.split(".", 1)
                items.append({"table": table, "field": field})
            elif kind == "required_behaviors":
                items.append({"behavior": value})
    return items


def _coerce_field_map(values: dict[Any, Any], *, kind: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for table, raw_fields in values.items():
        table_name = str(table).strip()
        if not table_name:
            continue
        if isinstance(raw_fields, str):
            fields = [part.strip() for part in re.split(r"[,;]", raw_fields) if part.strip()]
        elif isinstance(raw_fields, list):
            fields = raw_fields
        else:
            continue
        for field_item in fields:
            field_name = str(field_item.get("name") if isinstance(field_item, dict) else field_item).strip()
            if field_name:
                items.append({"table": table_name, "field": field_name})
    return items


def _append_model_property_artifacts(contract: dict[str, Any], values: list[Any]) -> None:
    for value in values:
        if not isinstance(value, dict):
            continue
        table = value.get("name") or value.get("table") or value.get("model")
        if not table:
            continue
        contract.setdefault("required_tables", []).append({"table": table})
        properties = value.get("properties") or value.get("fields") or value.get("columns") or []
        if isinstance(properties, list):
            for field in properties:
                if isinstance(field, dict):
                    name = field.get("name") or field.get("field")
                else:
                    name = str(field)
                if name:
                    contract.setdefault("required_fields", []).append({"table": table, "field": name})


def _compact_previous_response(data: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    raw_content = data.get("raw_content")
    if isinstance(raw_content, str) and raw_content:
        compact["raw_content_excerpt"] = raw_content[:1200]
        compact["raw_content_chars"] = len(raw_content)
    for key in ("summary", "status", "message", "error", "missing_info"):
        if key in data:
            compact[key] = data[key]
    if isinstance(data.get("requirements"), list):
        compact["requirements"] = data["requirements"][:6]
    raw_contract = data.get("artifact_contract")
    if isinstance(raw_contract, list):
        compact["artifact_contract"] = raw_contract[:8]
    elif isinstance(raw_contract, dict):
        compact["artifact_contract"] = {
            key: value[:8] if isinstance(value, list) else value
            for key, value in raw_contract.items()
            if key in ARTIFACT_KEYS or key in {"routes", "tables", "models", "fields", "filters", "relationships"}
        }
    if isinstance(data.get("patches"), list):
        compact["patches"] = data["patches"][:4]
    if isinstance(data.get("files"), list):
        compact["files"] = [
            {
                "path": item.get("path") or item.get("file") or item.get("filename"),
                "content_chars": len(str(item.get("content") or item.get("code") or "")),
            }
            for item in data["files"][:6]
            if isinstance(item, dict)
        ]
    compact["keys"] = sorted(data.keys())
    return compact


def _contract_delta_for_edit(index: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    delta = normalize_requirement_contract(
        {
            "source": contract.get("source") or "edit",
            "prompt": contract.get("prompt") or "",
            "requirements": contract.get("requirements") or [],
        }
    )
    kept = 0
    for key in ARTIFACT_KEYS:
        for item in contract.get(key) or []:
            single = normalize_requirement_contract({key: [item]})
            result = validate_artifacts(index, single).as_dict()
            if result.get("missing_artifacts") or key == "removed_artifacts":
                delta[key].append(item)
                kept += 1
    return delta if kept else contract


def _compact_contract_for_prompt(contract: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "requirements": [
            {
                "id": item.get("id"),
                "description": item.get("description"),
                "critical": item.get("critical", True),
            }
            for item in (contract.get("requirements") or [])[:10]
            if isinstance(item, dict)
        ]
    }
    for key in ARTIFACT_KEYS:
        values = contract.get(key) or []
        if values:
            compact[key] = values[:20]
    return compact


def _compact_file(path: Path, rel: str, *, max_chars: int = MAX_EDIT_FILE_CONTEXT_CHARS) -> dict[str, str]:
    text = read_project_file(path, rel)
    if len(text) <= max_chars:
        return {"path": rel, "content": text}
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return {
        "path": rel,
        "content": f"{head}\n\n# ... middle omitted for token budget ...\n\n{tail}",
    }


def _contract_terms(contract: dict[str, Any] | None) -> set[str]:
    terms: set[str] = set()
    if not contract:
        return terms
    for req in contract.get("requirements") or []:
        if isinstance(req, dict):
            for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(req.get("description") or "")):
                terms.add(word.lower())
    for key in ARTIFACT_KEYS:
        for item in contract.get(key) or []:
            if not isinstance(item, dict):
                continue
            for value in item.values():
                if isinstance(value, str):
                    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value):
                        terms.add(word.lower())
    return {
        term
        for term in terms
        if term not in {"the", "and", "for", "with", "that", "this", "none", "true", "false"}
    }


def _compact_file_for_patch(
    path: Path,
    rel: str,
    *,
    contract: dict[str, Any] | None,
    max_chars: int = MAX_PATCH_FILE_CONTEXT_CHARS,
) -> dict[str, str]:
    text = read_project_file(path, rel)
    if len(text) <= max_chars:
        return {"path": rel, "content": text}
    terms = _contract_terms(contract)
    if not terms:
        return _compact_file(path, rel, max_chars=max_chars)
    lines = text.splitlines(keepends=True)
    selected: set[int] = set(range(min(28, len(lines))))
    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            start = max(0, idx - 6)
            end = min(len(lines), idx + 14)
            selected.update(range(start, end))
    if len(selected) <= 28:
        return _compact_file(path, rel, max_chars=max_chars)
    chunks: list[str] = []
    last = -2
    for idx in sorted(selected):
        if idx != last + 1 and chunks:
            chunks.append("\n# ... source omitted for token budget ...\n")
        chunks.append(lines[idx])
        last = idx
        if sum(len(part) for part in chunks) >= max_chars:
            break
    return {"path": rel, "content": "".join(chunks)[:max_chars]}
