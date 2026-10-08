from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import ValidationError
from app.llm.file_spec import FileSpec
from app.services import model_project_pipeline as _common
from app.services.model_pipeline.capabilities import (
    build_capability_plan,
    build_pipeline_audit,
)
from app.services.model_pipeline.intent import (
    extract_prompt_intent,
    merge_intent_into_api_contract,
    merge_intent_into_schema_plan,
)
from app.services.model_pipeline.llm_io import (
    complete_json as _complete_json,
)
from app.services.model_pipeline.llm_io import (
    complete_json_with_repair as _complete_json_with_repair,
)
from app.services.model_pipeline.llm_io import (
    complete_text as _complete_text,
)
from app.services.model_pipeline.llm_io import (
    provider_label,
)
from app.services.model_pipeline.llm_io import (
    record_trace_event as _record_trace_event,
)
from app.services.model_pipeline.paths import (
    REQUIRED_PROJECT_FILES,
)
from app.services.model_pipeline.paths import (
    canonical_project_path as _canonical_project_path,
)
from app.services.model_pipeline.paths import (
    is_safe_file_path as _is_safe_file_path,
)
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.model_pipeline.quests import (
    build_generation_quests,
    build_quest_run_report,
    execute_rendered_quests,
    quest_dicts,
)
from app.services.model_pipeline.renderer import render_fastapi_sqlmodel_project
from app.services.model_pipeline.specs import (
    apply_resource_allowlist,
    build_canonical_project_spec,
    enrich_schema_plan_from_canonical_spec,
    merge_canonical_spec_into_contract,
)
from app.services.model_pipeline.types import ModelBuildResult, ModelRunUsage
from app.services.model_pipeline.validation import (
    better_candidate as _better_candidate,
)
from app.services.model_pipeline.validation import (
    candidate_quality_gate as _candidate_quality_gate,
)
from app.services.model_pipeline.validation import (
    validate_file_specs as _validate_file_specs,
)

_compact_previous_response = _common._compact_previous_response
_finalize_contract_data = _common._finalize_contract_data
_normalize_required_file_names = _common._normalize_required_file_names
_parse_marked_file_specs = _common._parse_marked_file_specs
_strip_markdown_file_fence = _common._strip_markdown_file_fence
_validate_schema_plan = _common._validate_schema_plan
_compact_contract_for_prompt = _common._compact_contract_for_prompt

FILE_ROLE_RULES = [
    f"{FASTAPI_PROFILE.entrypoint_file}: instantiate exactly one FastAPI app and wire routers/routes.",
    f"{FASTAPI_PROFILE.database_file}: database engine/session setup only; no FastAPI app and no route handlers.",
    "model files: define persistence models using SQLModel(table=True) or SQLAlchemy declarative models when database tables are required.",
    "schema files: define request/response Pydantic schemas only; do not replace database models with schemas.",
    "router files: define APIRouter endpoint handlers; do not instantiate FastAPI apps.",
    "auth files: define password/JWT helpers and dependencies; do not instantiate FastAPI apps or duplicate resource routers.",
    "All generated functions must contain real implementation code, not pass/TODO/placeholder comments.",
    "Every name used in route decorators, response_model, dependencies, type annotations, and defaults must be imported or defined.",
]

COMPLETE_PROJECT_MIN_FILES = 4
COMPLETE_PROJECT_MAX_FILES = 10


def _role_instruction(path: str) -> str:
    if path == FASTAPI_PROFILE.entrypoint_file:
        return (
            "This is the entrypoint. Instantiate FastAPI exactly once, import/wire routers when present, "
            "and keep resource handlers here only if the file plan does not use routers."
        )
    if path == FASTAPI_PROFILE.database_file:
        return (
            "This is the database file. Write only engine/session/base setup such as create_engine, SessionLocal, "
            "Base/SQLModel metadata helpers, and get_db/get_session. Do not instantiate FastAPI. Do not write route handlers."
        )
    if _is_model_plan_path(path):
        return (
            "This is the persistence model file. Define SQLModel(table=True) or SQLAlchemy declarative models for required tables. "
            "Do not instantiate FastAPI. Do not write route handlers. Do not use Pydantic-only schemas as database models."
        )
    if path.endswith("schemas.py"):
        return (
            "This is the schema file. Define request/response Pydantic schemas. Do not instantiate FastAPI and do not define database tables here."
        )
    if _is_router_plan_path(path):
        return (
            "This is a router file. Use APIRouter and real handler implementations. Do not instantiate FastAPI. "
            "Import dependencies/models/schemas needed by this router."
        )
    if path.endswith("auth.py"):
        return (
            "This is the auth helper file. Define password/JWT helpers and current-user dependencies. "
            "Do not instantiate FastAPI and do not duplicate resource CRUD routes."
        )
    return "Respect the target file role and do not duplicate unrelated project files."


def _compact_routes_for_file(api_contract: dict[str, Any], file_item: dict[str, Any], path: str) -> list[dict[str, Any]]:
    routes = api_contract.get("routes") or []
    if not isinstance(routes, list):
        routes = []
    related_routes = file_item.get("related_routes") or []
    route_keys = {
        (
            str(item.get("method") or "*").upper(),
            str(item.get("path") or item.get("route") or ""),
        )
        for item in related_routes
        if isinstance(item, dict)
    }
    if route_keys:
        routes = [
            route
            for route in routes
            if isinstance(route, dict)
            and (str(route.get("method") or "*").upper(), str(route.get("path") or "")) in route_keys
        ]
    compact: list[dict[str, Any]] = []
    for route in routes[:80]:
        if not isinstance(route, dict):
            continue
        compact.append(
            {
                "method": route.get("method"),
                "path": route.get("path"),
                "description": route.get("description"),
                "dependencies": route.get("dependencies") or route.get("auth"),
                "query_params": route.get("query_params") or route.get("parameters"),
                "body_schema": route.get("body_schema") or route.get("request_model"),
                "response_model": route.get("response_model"),
            }
        )
    if path != FASTAPI_PROFILE.entrypoint_file and len(compact) > 24:
        return compact[:24]
    return compact


def _compact_schema_for_file(schema_plan: dict[str, Any], file_item: dict[str, Any]) -> dict[str, Any]:
    schema = schema_plan.get("schema") if isinstance(schema_plan.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    related_tables = {
        str(value).strip().lower()
        for value in file_item.get("related_tables") or []
        if str(value).strip()
    }
    selected_tables = [
        table
        for table in tables
        if isinstance(table, dict)
        and (not related_tables or str(table.get("name") or table.get("table") or "").strip().lower() in related_tables)
    ]
    return {
        "domain": schema_plan.get("domain"),
        "tables": selected_tables[:30],
        "no_database_required": bool(schema.get("no_database_required") or schema_plan.get("no_database_required")),
    }


def _compact_api_for_file(api_contract: dict[str, Any], file_item: dict[str, Any], path: str) -> dict[str, Any]:
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {}
    if path in {FASTAPI_PROFILE.database_file, FASTAPI_PROFILE.dependency_file} or _is_model_plan_path(path):
        return {
            "summary": api_contract.get("summary") or api_contract.get("description"),
            "artifact_contract": _compact_contract_for_prompt(contract) if contract else {},
        }
    return {
        "summary": api_contract.get("summary") or api_contract.get("description"),
        "auth": api_contract.get("auth"),
        "websocket_events": (api_contract.get("websocket_events") or [])[:20],
        "routes": _compact_routes_for_file(api_contract, file_item, path),
        "artifact_contract": _compact_contract_for_prompt(contract) if contract else {},
    }


def _compact_file_plan_for_file(file_plan: dict[str, Any], file_item: dict[str, Any]) -> dict[str, Any]:
    files = [
        {
            "path": item.get("path"),
            "purpose": item.get("purpose"),
            "depends_on": item.get("depends_on") or [],
        }
        for item in (file_plan.get("files") or [])
        if isinstance(item, dict)
    ]
    return {"target_file": file_item, "files": files[:80], "notes": file_plan.get("notes")}


def _planned_path_for_item(item: dict[str, Any]) -> str:
    raw_path = _canonical_project_path(str(item.get("path") or ""))
    purpose = str(item.get("purpose") or "").lower()
    if (
        "/" not in raw_path
        and raw_path in FASTAPI_PROFILE.entrypoint_aliases
        and (
            not purpose
            or purpose == "model-selected project file."
            or any(term in purpose for term in ("entry", "main", "fastapi app", "application"))
        )
    ):
        return FASTAPI_PROFILE.entrypoint_file
    return raw_path


def _compact_dependency_requirements(api_contract: dict[str, Any], schema_plan: dict[str, Any]) -> dict[str, Any]:
    needs_database = bool(_schema_tables(schema_plan))
    auth = api_contract.get("auth")
    websocket_events = api_contract.get("websocket_events") or []
    return {
        "framework": "fastapi",
        "database": "sqlalchemy_or_sqlmodel" if needs_database else "",
        "auth": auth,
        "websocket": bool(websocket_events),
        "output_rules": [
            "Return package names only, one per line.",
            "Do not include Python code, comments, explanations, or repeated variants.",
            "Use stable common packages only.",
        ],
    }


def _clean_requirements_content(text: str) -> str | None:
    from app.services.model_pipeline.paths import looks_like_requirements_content

    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```") or " " in line or len(line) > 40:
            continue
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        lines.append(line)
        if len(lines) >= 20:
            break
    content = "\n".join(lines).strip() + "\n" if lines else ""
    if not any(line.lower().startswith("fastapi") for line in lines):
        return None
    return content if looks_like_requirements_content(content) else None


def _artifact_marker(item: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(item.get(key) or "").strip().lower() for key in keys)


def _append_unique_artifact(contract: dict[str, Any], key: str, item: dict[str, Any], keys: tuple[str, ...]) -> None:
    values = contract.setdefault(key, [])
    marker = _artifact_marker(item, keys)
    if not any(_artifact_marker(existing, keys) == marker for existing in values if isinstance(existing, dict)):
        values.append({name: value for name, value in item.items() if value not in (None, "")})


def _schema_tables(schema_plan: dict[str, Any]) -> list[dict[str, Any]]:
    schema = schema_plan.get("schema") if isinstance(schema_plan.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    return [table for table in tables if isinstance(table, dict)]


def _has_schema_tables(schema_plan: dict[str, Any]) -> bool:
    return bool(_schema_tables(schema_plan))


def _fallback_schema_plan_from_prompt(prompt: str) -> dict[str, Any] | None:
    intent = extract_prompt_intent(prompt)
    contract = intent.get("contract") if isinstance(intent.get("contract"), dict) else {}
    additions = intent.get("schema_additions") if isinstance(intent.get("schema_additions"), dict) else {}
    tables_by_name: dict[str, dict[str, Any]] = {}

    for item in contract.get("required_tables") or []:
        if not isinstance(item, dict):
            continue
        table_name = _schema_identifier(item.get("table") or item.get("name"))
        if table_name:
            tables_by_name.setdefault(table_name, {"name": table_name, "columns": []})
    for table in additions.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_name = _schema_identifier(table.get("name") or table.get("table"))
        if not table_name:
            continue
        target = tables_by_name.setdefault(table_name, {"name": table_name, "columns": []})
        for column in table.get("columns") or table.get("fields") or []:
            if isinstance(column, dict):
                _append_schema_column(target, column)

    if not tables_by_name:
        return None

    for table in tables_by_name.values():
        if not any(_schema_identifier(column.get("name")) == "id" for column in table.get("columns") or []):
            table["columns"] = [{"name": "id", "type": "INTEGER"}, *(table.get("columns") or [])]
    tables = list(tables_by_name.values())
    return {
        "domain": "generated_service",
        "ddl_sql": " ".join(_create_table_statement(table) for table in tables),
        "schema": {"tables": tables, "no_database_required": False},
        "assumptions": ["Schema plan was derived from explicit resource and filter terms in the user request."],
    }


def _append_schema_column(table: dict[str, Any], column: dict[str, Any]) -> None:
    name = _schema_identifier(column.get("name") or column.get("field") or column.get("column"))
    if not name:
        return
    columns = table.setdefault("columns", [])
    for existing in columns:
        if isinstance(existing, dict) and _schema_identifier(existing.get("name")) == name:
            if column.get("filter") or column.get("filterable") or column.get("index"):
                existing["filter"] = True
                existing["index"] = True
            if column.get("type") and not existing.get("type"):
                existing["type"] = column["type"]
            return
    item = {"name": name, "type": column.get("type") or _schema_type_from_name(name)}
    if column.get("filter") or column.get("filterable") or column.get("index"):
        item["filter"] = True
        item["index"] = True
    columns.append(item)


def _create_table_statement(table: dict[str, Any]) -> str:
    table_name = _schema_identifier(table.get("name") or table.get("table"))
    columns = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        name = _schema_identifier(column.get("name") or column.get("field") or column.get("column"))
        if not name:
            continue
        suffix = " PRIMARY KEY" if name == "id" else ""
        columns.append(f"{name} {_schema_sql_type(column.get('type'))}{suffix}")
    if not columns:
        columns.append("id INTEGER PRIMARY KEY")
    return f"CREATE TABLE {table_name} ({', '.join(columns)});"


def _schema_sql_type(value: Any) -> str:
    text = str(value or "").lower()
    if any(term in text for term in ("int", "serial", "bigint", "smallint")):
        return "INTEGER"
    if any(term in text for term in ("float", "double", "decimal", "numeric", "real")):
        return "REAL"
    if "bool" in text:
        return "BOOLEAN"
    if "datetime" in text or "timestamp" in text:
        return "TIMESTAMP"
    return "TEXT"


def _schema_type_from_name(name: str) -> str:
    if any(term in name for term in ("price", "amount", "cost", "total", "score", "rate")):
        return "REAL"
    if name.endswith("_id") or name in {"count", "quantity", "limit", "page"}:
        return "INTEGER"
    if name.startswith("is_") or name in {"paid", "done", "enabled", "approved", "active", "published"}:
        return "BOOLEAN"
    if "date" in name:
        return "TIMESTAMP"
    return "TEXT"


def _schema_identifier(value: Any) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if text and text[0].isdigit():
        text = f"field_{text}"
    return text


def _is_model_plan_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return name in {"model.py", "models.py", "db_models.py"} or name.endswith("_models.py") or name.endswith("_model.py")


def _is_router_plan_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return path.startswith("routers/") or name in {"router.py", "routes.py"} or name.endswith("_router.py") or name.endswith("_routes.py")


def _planned_paths(file_plan: dict[str, Any]) -> list[str]:
    return [
        str(item.get("path") or "")
        for item in (file_plan.get("files") or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]


def _complete_project_gap(files: list[FileSpec], file_plan: dict[str, Any]) -> dict[str, Any]:
    planned = _planned_paths(file_plan)
    required_paths = sorted(set(planned) | REQUIRED_PROJECT_FILES)
    candidate_paths = [item.path for item in files]
    missing = sorted(path for path in required_paths if path not in set(candidate_paths))
    return {
        "complete": not missing,
        "planned_count": len(planned),
        "candidate_count": len(candidate_paths),
        "planned_paths": planned,
        "candidate_paths": candidate_paths,
        "missing_paths": missing,
    }


def _column_name(column: Any) -> str:
    if isinstance(column, dict):
        return str(column.get("name") or column.get("field") or column.get("column") or "").strip()
    return str(column or "").strip()


def _table_name(table: dict[str, Any]) -> str:
    return str(table.get("name") or table.get("table") or table.get("model") or "").strip()


def _schema_filter_fields(table: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for item in table.get("search_fields") or table.get("filters") or table.get("filter_fields") or []:
        if isinstance(item, dict):
            raw_columns = item.get("columns") or item.get("fields") or item.get("field") or item.get("name")
            if isinstance(raw_columns, list):
                fields.update(_column_name(column) for column in raw_columns)
            else:
                fields.add(_column_name(raw_columns))
        else:
            fields.add(_column_name(item))
    for column in table.get("columns") or table.get("fields") or []:
        if isinstance(column, dict) and any(column.get(key) for key in ("index", "indexed", "filter", "searchable")):
            fields.add(_column_name(column))
    fields.discard("")
    return fields


def _enrich_contract_from_schema_plan(contract: dict[str, Any], schema_plan: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(contract)
    for key in ("required_tables", "required_fields", "required_filters", "required_relationships"):
        enriched[key] = list(enriched.get(key) or [])
    for table in _schema_tables(schema_plan):
        table_name = _table_name(table)
        if not table_name:
            continue
        _append_unique_artifact(enriched, "required_tables", {"table": table_name}, ("table",))
        for column in table.get("columns") or table.get("fields") or []:
            field_name = _column_name(column)
            if not field_name:
                continue
            _append_unique_artifact(
                enriched,
                "required_fields",
                {"table": table_name, "field": field_name},
                ("table", "field"),
            )
            if isinstance(column, dict):
                reference = column.get("references") or column.get("foreign_key")
                if reference:
                    target_table = str(reference).split("(", 1)[0].split(".", 1)[0].strip()
                    if target_table:
                        _append_unique_artifact(
                            enriched,
                            "required_relationships",
                            {"from_table": table_name, "field": field_name, "to_table": target_table},
                            ("from_table", "field", "to_table"),
                        )
        for field_name in _schema_filter_fields(table):
            _append_unique_artifact(
                enriched,
                "required_filters",
                {"table": table_name, "field": field_name},
                ("table", "field"),
            )
        for rel in table.get("relationships") or table.get("foreign_keys") or []:
            if not isinstance(rel, dict):
                continue
            field_name = str(
                rel.get("field")
                or rel.get("field_name")
                or rel.get("column")
                or rel.get("column_name")
                or rel.get("from_field")
                or rel.get("from_column")
                or ""
            ).strip()
            target_table = str(
                rel.get("related_table")
                or rel.get("related_table_name")
                or rel.get("to_table")
                or rel.get("to_table_name")
                or rel.get("references_table")
                or rel.get("references_table_name")
                or rel.get("target_table")
                or rel.get("target_table_name")
                or rel.get("to")
                or ""
            ).strip()
            if target_table:
                _append_unique_artifact(
                    enriched,
                    "required_relationships",
                    {"from_table": table_name, "field": field_name, "to_table": target_table},
                    ("from_table", "field", "to_table"),
                )
    return enriched


def _looks_like_direct_file_content(path: str, text: str) -> bool:
    stripped = _strip_markdown_file_fence(text or "").strip()
    if not stripped:
        return False
    lowered = stripped[:500].lower()
    if lowered.startswith(("the provided", "here is", "here's", "this file", "explanation", "working summary")):
        return False
    if path.endswith(".py"):
        return bool(re.search(r"(?m)^\s*(from\s+\S+\s+import|import\s+\S+|class\s+\w+|def\s+\w+|async\s+def\s+\w+)", stripped))
    if path == FASTAPI_PROFILE.dependency_file:
        meaningful_lines = [
            line.strip()
            for line in stripped.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return bool(meaningful_lines) and all(re.match(r"^[A-Za-z0-9_.-]+(?:[<>=!~]=?.*)?$", line) for line in meaningful_lines[:30])
    return False


def _file_spec_from_model_text(text: str, path: str) -> FileSpec | None:
    files = _parse_marked_file_specs(text)
    spec = next((item for item in files if item.path == path), files[0] if len(files) == 1 else None)
    if spec is not None:
        return FileSpec(path=path, content=spec.content)
    if _looks_like_direct_file_content(path, text):
        return FileSpec(path=path, content=_strip_markdown_file_fence(text).strip())
    return None


def _file_summary(spec: FileSpec, *, purpose: str = "") -> dict[str, Any]:
    return {
        "path": spec.path,
        "chars": len(spec.content),
        "purpose": purpose,
        "exports_hint": re.findall(r"(?m)^(?:class|def)\s+([A-Za-z_][\w]*)", spec.content)[:20],
    }


def _compact_file_plan_for_project(file_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "files": [
            {
                "path": item.get("path"),
                "purpose": item.get("purpose"),
                "depends_on": item.get("depends_on") or [],
                "related_tables": item.get("related_tables") or [],
                "related_routes": item.get("related_routes") or [],
                "role_instruction": _role_instruction(str(item.get("path") or "")),
            }
            for item in (file_plan.get("files") or [])
            if isinstance(item, dict)
        ][:COMPLETE_PROJECT_MAX_FILES],
        "notes": file_plan.get("notes"),
    }


def _schema_table_count(schema_plan: dict[str, Any]) -> int:
    schema = schema_plan.get("schema") if isinstance(schema_plan.get("schema"), dict) else {}
    return len([table for table in schema.get("tables") or [] if isinstance(table, dict)])


def _deterministic_api_contract(prompt: str, schema_plan: dict[str, Any], prompt_intent: dict[str, Any]) -> dict[str, Any] | None:
    contract = prompt_intent.get("contract") if isinstance(prompt_intent.get("contract"), dict) else {}
    if _schema_table_count(schema_plan) < 2:
        return None
    if not contract.get("required_routes") or not contract.get("required_tables"):
        return None
    auth_policy = prompt_intent.get("auth_policy") if isinstance(prompt_intent.get("auth_policy"), dict) else {}
    if not (auth_policy.get("files") or auth_policy.get("websocket") or auth_policy.get("public_resources") or contract.get("required_filters")):
        return None
    requirements = [
        {
            "id": "R1",
            "kind": "project",
            "description": "Deterministic CRUD contract derived from the user request and schema plan.",
            "critical": True,
        }
    ]
    return {
        "project_name": str(schema_plan.get("domain") or "generated_project"),
        "summary": "FastAPI resource backend generated from deterministic prompt intent.",
        "requirements": requirements,
        "routes": list(contract.get("required_routes") or []),
        "auth": None,
        "websocket_events": [],
        "artifact_contract": _enrich_contract_from_schema_plan(contract, schema_plan),
        "assumptions": schema_plan.get("assumptions") or [],
        "auth_policy": auth_policy,
        "deterministic": True,
    }


def _renderer_file_plan(schema_plan: dict[str, Any], api_contract: dict[str, Any], canonical_spec: dict[str, Any]) -> dict[str, Any]:
    tables = [_table_name(table) for table in _schema_tables(schema_plan) if _table_name(table)]
    routes = api_contract.get("routes") if isinstance(api_contract.get("routes"), list) else []
    files: list[dict[str, Any]] = [
        {
            "path": FASTAPI_PROFILE.entrypoint_file,
            "purpose": "FastAPI application entrypoint and router wiring.",
            "depends_on": [],
            "related_tables": tables,
            "related_routes": routes[:80],
        },
        {
            "path": FASTAPI_PROFILE.database_file,
            "purpose": "SQLModel database engine, session dependency, and schema bootstrap helpers.",
            "depends_on": [],
            "related_tables": tables,
            "related_routes": [],
        },
        {
            "path": "models.py",
            "purpose": "SQLModel persistence models for canonical resources.",
            "depends_on": [FASTAPI_PROFILE.database_file],
            "related_tables": tables,
            "related_routes": [],
        },
        {
            "path": "schemas.py",
            "purpose": "Pydantic request and response schemas for canonical resources.",
            "depends_on": ["models.py"],
            "related_tables": tables,
            "related_routes": [],
        },
        {
            "path": "routers/__init__.py",
            "purpose": "Router package marker.",
            "depends_on": [],
            "related_tables": [],
            "related_routes": [],
        },
    ]
    for resource in canonical_spec.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        table = str(resource.get("table") or "").strip()
        if not table:
            continue
        base_path = f"/{table}"
        files.append(
            {
                "path": f"routers/{table}.py",
                "purpose": f"CRUD router for canonical {table} resource.",
                "depends_on": [FASTAPI_PROFILE.database_file, "models.py", "schemas.py"],
                "related_tables": [table],
                "related_routes": [
                    route
                    for route in routes
                    if isinstance(route, dict) and str(route.get("path") or "").startswith(base_path)
                ],
            }
        )
    if api_contract.get("auth") or "auth.py" in {str(item.get("path") or "") for item in files}:
        files.append(
            {
                "path": "auth.py",
                "purpose": "Authentication helpers and current-user dependency.",
                "depends_on": [],
                "related_tables": [],
                "related_routes": [],
            }
        )
    if api_contract.get("websocket_events") or any("websocket" in str(term) for term in canonical_spec.get("request_terms") or []):
        files.append(
            {
                "path": "websockets.py",
                "purpose": "Websocket notification manager and broadcast helpers.",
                "depends_on": [],
                "related_tables": [],
                "related_routes": [],
            }
        )
    files.append(
        {
            "path": FASTAPI_PROFILE.dependency_file,
            "purpose": "Runtime dependencies.",
            "depends_on": [],
            "related_tables": [],
            "related_routes": [],
        }
    )
    return {"files": files, "notes": ["deterministic renderer file plan"]}


def _renderer_coverage(canonical_spec: dict[str, Any], api_contract: dict[str, Any]) -> dict[str, Any]:
    raw_artifact = api_contract.get("artifact_contract")
    artifact = raw_artifact if isinstance(raw_artifact, dict) else {}
    capability_plan = build_capability_plan(canonical_spec, artifact)
    custom_routes = [item for item in artifact.get("custom_routes") or [] if isinstance(item, dict)]
    unsupported = [item.get("capability_id") for item in capability_plan.get("unsupported_for_renderer") or []]
    covered = (
        bool(canonical_spec.get("resources"))
        and not unsupported
    )
    return {
        "covered": covered,
        "reason": "canonical_spec_supported_by_sqlmodel_renderer" if covered else "requires_model_codegen",
        "unsupported_operations": sorted(str(item) for item in unsupported if item),
        "custom_route_count": len(custom_routes),
        "capability_plan": capability_plan,
    }


def _validation_repair_plan(validation: dict[str, Any], canonical_spec: dict[str, Any]) -> dict[str, Any]:
    missing_artifacts = ((validation.get("artifact_validation") or {}).get("missing_artifacts") or [])[:30]
    checks = [check for check in (validation.get("static") or {}).get("checks") or [] if isinstance(check, dict) and not check.get("passed")]
    affected: set[str] = set()
    for check in checks:
        path = str(check.get("path") or check.get("file") or "")
        if path:
            affected.add(path)
        error = str(check.get("error") or "").lower()
        if "database" in error or "get_db" in error or "get_session" in error:
            affected.add(FASTAPI_PROFILE.database_file)
        if "undefined" in error and path:
            affected.add(path)
    for item in missing_artifacts:
        text = str(item).lower()
        if "database" in text or "table" in text or "field" in text:
            affected.update({"models.py", "schemas.py"})
        if "route" in text:
            for resource in canonical_spec.get("resources") or []:
                if isinstance(resource, dict) and resource.get("table") and str(resource.get("table")).lower() in text:
                    affected.add(f"routers/{resource['table']}.py")
            affected.add(FASTAPI_PROFILE.entrypoint_file)
    return {
        "failure_category": validation.get("failure_category") or "",
        "affected_files": sorted(affected),
        "static_errors": [{"path": item.get("path") or item.get("file"), "error": item.get("error")} for item in checks[:12]],
        "missing_artifacts": missing_artifacts,
    }


def _merge_repaired_file_specs(original: list[FileSpec], repaired: list[FileSpec]) -> list[FileSpec]:
    if not repaired:
        return original
    by_path = {item.path: item for item in original}
    order = [item.path for item in original]
    for item in repaired:
        if item.path not in by_path:
            order.append(item.path)
        by_path[item.path] = item
    return [by_path[path] for path in order if path in by_path]


class ModelOwnedGenerationPipeline:
    def __init__(self, provider: Any):
        self.provider = provider
        self.trace: list[dict[str, Any]] = []

    def plan_schema(self, prompt: str, *, existing_context: str = "") -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "You are a backend data architect. Produce only the database/schema plan for the requested FastAPI backend. "
            "Do not write application code. Do not choose a fixed template. Do not add features the user did not request. "
            "Return ONLY JSON with keys: domain, ddl_sql, schema, assumptions. "
            "schema.tables must include table name, columns with type/nullability/default when known, relationships, "
            "indexes, and filters/search fields requested by the user."
        )
        data, usage = _complete_json_with_repair(
            self.provider,
            system,
            {"request": prompt, "existing_project_context": existing_context},
            required_keys=["domain", "ddl_sql", "schema", "assumptions"],
            max_tokens=3600,
            repair_max_tokens=4200,
            trace=self.trace,
            stage="generation.schema",
        )
        validation = _validate_schema_plan(data)
        data["validation"] = validation
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.schema_validation",
                "status": "accepted" if validation["passed"] else "rejected",
                "tables": validation.get("tables") or [],
                "errors": validation.get("errors") or [],
                "warnings": validation.get("warnings") or [],
            },
        )
        if not validation["passed"]:
            fallback = _fallback_schema_plan_from_prompt(prompt)
            if fallback is not None:
                fallback_validation = _validate_schema_plan(fallback)
                fallback["validation"] = fallback_validation
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "generation.schema_prompt_fallback",
                        "status": "accepted" if fallback_validation["passed"] else "rejected",
                        "tables": fallback_validation.get("tables") or [],
                        "errors": fallback_validation.get("errors") or [],
                        "warnings": fallback_validation.get("warnings") or [],
                    },
                )
                if fallback_validation["passed"]:
                    return fallback, usage
            repair_system = (
                "Repair the database/schema plan JSON. Return ONLY JSON with keys: domain, ddl_sql, schema, assumptions. "
                "Keep the user's requested behavior unchanged. Ensure ddl_sql CREATE TABLE statements and schema.tables match."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    "request": prompt,
                    "existing_project_context": existing_context,
                    "previous_response": _compact_previous_response(data),
                    "validation_errors": validation.get("errors") or [],
                },
                max_tokens=4200,
                trace=self.trace,
                stage="generation.schema_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            validation = _validate_schema_plan(repaired)
            repaired["validation"] = validation
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.schema_validation.repair",
                    "status": "accepted" if validation["passed"] else "rejected",
                    "tables": validation.get("tables") or [],
                    "errors": validation.get("errors") or [],
                    "warnings": validation.get("warnings") or [],
                },
            )
            data = repaired
        if not data.get("validation", {}).get("passed"):
            raise ValidationError("model did not return a usable schema plan")
        return data, usage

    def plan_api_contract(self, prompt: str, schema_plan: dict[str, Any]) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "You are a FastAPI API contract planner. Use the saved schema plan as source context. "
            "Do not write code. Do not invent fixed template APIs. Return ONLY JSON with keys: summary, requirements, "
            "routes, auth, websocket_events, artifact_contract, assumptions. "
            "artifact_contract must explicitly list routes, tables, fields, filters, relationships, and behaviors that prove completion."
        )
        data, usage = _complete_json_with_repair(
            self.provider,
            system,
            {"request": prompt, "schema_plan": schema_plan},
            required_keys=["summary", "requirements", "routes", "auth", "websocket_events", "artifact_contract", "assumptions"],
            max_tokens=4200,
            repair_max_tokens=4800,
            trace=self.trace,
            stage="generation.api_contract",
        )
        data = _finalize_contract_data(data, prompt=prompt, source="generation")
        requirements = data["requirements"]
        artifact_contract = _enrich_contract_from_schema_plan(
            data["artifact_contract"],
            schema_plan,
        )
        data["requirements"] = requirements
        data["artifact_contract"] = artifact_contract
        data["validation"] = {"passed": True}
        return data, usage

    def plan_files(
        self,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
    ) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "You are planning a maintainable FastAPI project file structure. Do not write code. "
            "Use the schema and API contract as the source of truth. Return ONLY JSON with keys: files, notes. "
            "files must be a list of objects with path, purpose, depends_on, related_tables, related_routes. "
            "Include the runnable entry/database/dependency files needed for the project. "
            f"The required entrypoint is {FASTAPI_PROFILE.entrypoint_file}; do not use root app.py as an extra app file. "
            "Respect normal FastAPI file roles: database setup belongs in the database file, app setup belongs in the entrypoint, "
            "database models belong in model files, and route handlers belong in router or entrypoint files."
        )
        data, usage = _complete_json_with_repair(
            self.provider,
            system,
            {"request": prompt, "schema_plan": schema_plan, "api_contract": api_contract},
            required_keys=["files", "notes"],
            max_tokens=2600,
            repair_max_tokens=3200,
            trace=self.trace,
            stage="generation.file_plan",
        )
        data = self._normalize_file_plan(data, allow_empty=True, schema_plan=schema_plan)
        files = data["files"]
        missing_required = sorted(REQUIRED_PROJECT_FILES - {item["path"] for item in files})
        if missing_required:
            repair_system = (
                "Repair the FastAPI file plan JSON. Return ONLY JSON with keys files and notes. "
                "Keep the same architecture, but include every missing required runnable file."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    "request": prompt,
                    "schema_plan": schema_plan,
                    "api_contract": api_contract,
                    "previous_response": data,
                    "missing_required_files": missing_required,
                },
                max_tokens=3000,
                trace=self.trace,
                stage="generation.file_plan_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            data = repaired
            data = self._normalize_file_plan(data, allow_empty=True, schema_plan=schema_plan)
            files = data["files"]
            missing_required = sorted(REQUIRED_PROJECT_FILES - {item["path"] for item in files})
            if missing_required:
                data["files"] = self._fallback_file_plan_files(
                    existing=files,
                    schema_plan=schema_plan,
                    api_contract=api_contract,
                    missing_required=missing_required,
                )
                data["fallback_required_files"] = True
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "generation.file_plan_fallback",
                        "status": "ok",
                        "files": [item["path"] for item in data["files"]],
                        "reason": "model file plan repair still missed required runnable files",
                    },
                )
                return data, usage
        data["files"] = files
        return data, usage

    def _normalize_file_plan(
        self,
        data: dict[str, Any],
        *,
        allow_empty: bool = False,
        schema_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_files: Any = data.get("files")
        if raw_files is None:
            raw_files = data.get("file_paths") or data.get("paths") or data.get("file_tree")
        if isinstance(raw_files, dict):
            normalized_raw: list[dict[str, Any]] = []
            for path, value in raw_files.items():
                if isinstance(value, dict):
                    normalized_raw.append({"path": path, **value})
                else:
                    normalized_raw.append({"path": path, "purpose": str(value or "")})
            raw_files = normalized_raw
        if isinstance(raw_files, list):
            normalized_list: list[dict[str, Any]] = []
            for item in raw_files:
                if isinstance(item, dict):
                    normalized_list.append(item)
                elif isinstance(item, str):
                    normalized_list.append({"path": item, "purpose": ""})
            raw_files = normalized_list
        else:
            raw_files = []
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            path = _canonical_project_path(str(item.get("path") or ""))
            path = _planned_path_for_item({**item, "path": path})
            if path in seen or not _is_safe_file_path(path):
                continue
            seen.add(path)
            files.append(
                {
                    "path": path,
                    "purpose": str(item.get("purpose") or ""),
                    "depends_on": [
                        _canonical_project_path(str(dep))
                        for dep in item.get("depends_on") or []
                        if str(dep).strip()
                    ],
                    "related_tables": [str(value) for value in item.get("related_tables") or []],
                    "related_routes": item.get("related_routes") or [],
                }
            )
        if not files and not allow_empty:
            raise ValidationError("model did not return a usable file plan")
        if schema_plan and _has_schema_tables(schema_plan) and not any(_is_model_plan_path(item["path"]) for item in files):
            table_names = [_table_name(table) for table in _schema_tables(schema_plan) if _table_name(table)]
            files.append(
                {
                    "path": "models.py",
                    "purpose": "Persistence database models for the planned schema tables.",
                    "depends_on": [FASTAPI_PROFILE.database_file],
                    "related_tables": table_names,
                    "related_routes": [],
                }
            )
        data["files"] = files
        return data

    def _fallback_file_plan_files(
        self,
        *,
        existing: list[dict[str, Any]],
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        missing_required: list[str],
    ) -> list[dict[str, Any]]:
        files = list(existing)
        existing_paths = {item["path"] for item in files}
        table_names = [
            str(table.get("name") or table.get("table") or "")
            for table in ((schema_plan.get("schema") or {}).get("tables") or [])
            if isinstance(table, dict)
        ]
        routes = api_contract.get("routes") or (api_contract.get("artifact_contract") or {}).get("required_routes") or []
        purposes = {
            FASTAPI_PROFILE.entrypoint_file: "FastAPI application entrypoint and route registration for the planned API contract.",
            FASTAPI_PROFILE.database_file: "Database engine/session setup and schema connection utilities for the planned schema.",
            FASTAPI_PROFILE.dependency_file: "Runtime package dependencies required by the generated FastAPI backend.",
            "models.py": "Persistence database models for the planned schema tables.",
        }
        if table_names and "models.py" not in existing_paths and not any(_is_model_plan_path(item["path"]) for item in files):
            files.append(
                {
                    "path": "models.py",
                    "purpose": purposes["models.py"],
                    "depends_on": [FASTAPI_PROFILE.database_file],
                    "related_tables": table_names,
                    "related_routes": [],
                }
            )
            existing_paths.add("models.py")
        for path in missing_required:
            if path in existing_paths:
                continue
            files.append(
                {
                    "path": path,
                    "purpose": purposes.get(path, "Required runnable project file."),
                    "depends_on": [],
                    "related_tables": table_names,
                    "related_routes": routes,
                }
            )
        return files

    def generate_planned_file(
        self,
        *,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        file_plan: dict[str, Any],
        file_item: dict[str, Any],
        previous_summaries: list[dict[str, Any]],
    ) -> tuple[FileSpec, ModelRunUsage]:
        path = _canonical_project_path(str(file_item.get("path") or ""))
        path = _planned_path_for_item({**file_item, "path": path})
        if path == FASTAPI_PROFILE.dependency_file:
            return self.generate_dependency_file(
                prompt=prompt,
                schema_plan=schema_plan,
                api_contract=api_contract,
                file_item={**file_item, "path": path},
            )
        system = (
            "You are writing exactly one file for a FastAPI backend. Use the saved schema plan, API contract, "
            "overall file plan, and summaries of already-written files. Do not write unrelated files. "
            "Do not summarize, explain, or describe the plan. Output only complete file content inside this wrapper:\n"
            f"### FILE: {path} ###\n<complete file content>\n### END FILE ###"
            f"\nTarget role: {_role_instruction(path)}"
        )
        compact_payload = {
            "request": prompt,
            "schema_context": _compact_schema_for_file(schema_plan, file_item),
            "api_context": _compact_api_for_file(api_contract, file_item, path),
            "file_plan_context": _compact_file_plan_for_file(file_plan, file_item),
            "target_file": file_item,
            "file_role_rules": FILE_ROLE_RULES,
            "previous_file_summaries": previous_summaries[-12:],
            "output_contract": {
                "required_path": path,
                "wrapper_start": f"### FILE: {path} ###",
                "wrapper_end": "### END FILE ###",
                "no_prose": True,
            },
        }
        text, usage = _complete_text(
            self.provider,
            system,
            compact_payload,
            max_tokens=6500,
            trace=self.trace,
            stage=f"generation.file.{path.replace('/', '.')}",
        )
        spec = _file_spec_from_model_text(text, path)
        if spec is None:
            repair_system = (
                "The previous response did not contain the required file. "
                "Write exactly one complete file for the requested target. "
                "Do not explain, summarize, or mention the file plan. "
                "Return only this wrapper:\n"
                f"### FILE: {path} ###\n<complete file content>\n### END FILE ###"
            )
            repaired_text, repair_usage = _complete_text(
                self.provider,
                repair_system,
                {
                    **compact_payload,
                    "invalid_previous_response_excerpt": text[:4000],
                    "parse_error": f"missing complete content for {path}",
                },
                max_tokens=6500,
                temperature=0.0,
                trace=self.trace,
                stage=f"generation.file.{path.replace('/', '.')}.format_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            spec = _file_spec_from_model_text(repaired_text, path)
        if spec is None:
            _record_trace_event(
                self.trace,
                {
                    "stage": f"generation.file.{path.replace('/', '.')}.parse",
                    "status": "rejected",
                    "reason": "model did not return target file content after format repair",
                },
            )
            raise ValidationError(f"model did not return complete content for `{path}`")
        return spec, usage

    def generate_dependency_file(
        self,
        *,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        file_item: dict[str, Any],
    ) -> tuple[FileSpec, ModelRunUsage]:
        path = FASTAPI_PROFILE.dependency_file
        system = (
            "You are writing only requirements.txt for a generated FastAPI backend. "
            "Return only package names, one per line. No prose, no code fences, no duplicates, no long package families."
        )
        text, usage = _complete_text(
            self.provider,
            system,
            {
                "request": prompt,
                "dependency_requirements": _compact_dependency_requirements(api_contract, schema_plan),
                "target_file": file_item,
            },
            max_tokens=700,
            temperature=0.0,
            trace=self.trace,
            stage=f"generation.file.{path.replace('/', '.')}",
        )
        content = _clean_requirements_content(text)
        if content is None:
            repair_text, repair_usage = _complete_text(
                self.provider,
                system,
                {
                    "request": prompt,
                    "invalid_previous_response_excerpt": text[:1500],
                    "required_packages_hint": ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "python-jose", "passlib"],
                    "target_file": file_item,
                },
                max_tokens=400,
                temperature=0.0,
                trace=self.trace,
                stage=f"generation.file.{path.replace('/', '.')}.format_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            content = _clean_requirements_content(repair_text)
        if content is None:
            _record_trace_event(
                self.trace,
                {
                    "stage": f"generation.file.{path.replace('/', '.')}.parse",
                    "status": "rejected",
                    "reason": "model did not return usable dependency list",
                },
            )
            raise ValidationError("model did not return usable requirements.txt content")
        return FileSpec(path=path, content=content), usage

    def generate_planned_files(
        self,
        *,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        file_plan: dict[str, Any],
    ) -> tuple[list[FileSpec], list[dict[str, Any]], ModelRunUsage]:
        usage = ModelRunUsage()
        generated: list[FileSpec] = []
        summaries: list[dict[str, Any]] = []
        for item in file_plan.get("files") or []:
            if not isinstance(item, dict):
                continue
            spec, file_usage = self.generate_planned_file(
                prompt=prompt,
                schema_plan=schema_plan,
                api_contract=api_contract,
                file_plan=file_plan,
                file_item=item,
                previous_summaries=summaries,
            )
            usage.add_usage(file_usage)
            generated.append(spec)
            summaries.append(_file_summary(spec, purpose=str(item.get("purpose") or "")))
        return _normalize_required_file_names(generated), summaries, usage

    def should_try_complete_project(self, file_plan: dict[str, Any]) -> bool:
        file_count = len([item for item in file_plan.get("files") or [] if isinstance(item, dict)])
        return COMPLETE_PROJECT_MIN_FILES <= file_count <= COMPLETE_PROJECT_MAX_FILES and "ollama" not in self.provider.__class__.__name__.lower()

    def generate_complete_project(
        self,
        *,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        file_plan: dict[str, Any],
    ) -> tuple[list[FileSpec], list[dict[str, Any]], dict[str, Any], ModelRunUsage]:
        planned_paths = [
            str(item.get("path") or "")
            for item in (file_plan.get("files") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        system = (
            "You are writing a complete FastAPI backend project from the saved schema plan, API contract, and file plan. "
            "Return only file wrappers, no prose, no markdown code fences, and no explanations. "
            f"Include complete contents for every planned file and for {FASTAPI_PROFILE.required_files_text}. "
            f"The only root FastAPI entrypoint is {FASTAPI_PROFILE.entrypoint_file}; do not create root app.py. "
            "Keep file roles separated: database setup in the database file, persistence models in model files, "
            "Pydantic request/response schemas in schema files, routers in router files, and app wiring in the entrypoint. "
            "Implement real code for every generated function. Do not use pass, TODO, placeholder text, or undefined names. "
            "Use this exact wrapper for each file:\n"
            "### FILE: path/to/file.py ###\n<complete file content>\n### END FILE ###"
        )
        text, usage = _complete_text(
            self.provider,
            system,
            {
                "request": prompt,
                "schema_context": {
                    "domain": schema_plan.get("domain"),
                    "schema": schema_plan.get("schema"),
                    "ddl_sql": schema_plan.get("ddl_sql"),
                    "assumptions": schema_plan.get("assumptions") or [],
                },
                "api_context": {
                    "summary": api_contract.get("summary") or api_contract.get("description"),
                    "requirements": (api_contract.get("requirements") or [])[:20],
                    "auth": api_contract.get("auth"),
                    "websocket_events": (api_contract.get("websocket_events") or [])[:20],
                    "routes": (api_contract.get("routes") or [])[:80],
                    "artifact_contract": _compact_contract_for_prompt(api_contract.get("artifact_contract") or {}),
                },
                "file_plan": _compact_file_plan_for_project(file_plan),
                "planned_paths": planned_paths,
                "required_paths": sorted(REQUIRED_PROJECT_FILES),
                "file_role_rules": FILE_ROLE_RULES,
                "output_contract": {
                    "wrapper_start": "### FILE: path/to/file.py ###",
                    "wrapper_end": "### END FILE ###",
                    "no_prose": True,
                    "no_code_fences": True,
                },
            },
            max_tokens=12000,
            trace=self.trace,
            stage="generation.complete_project",
        )
        files = _normalize_required_file_names(_parse_marked_file_specs(text))
        summaries = [_file_summary(spec, purpose="complete-project candidate") for spec in files]
        debug = {
            "raw_chars": len(text),
            "file_count": len(files),
            "planned_paths": planned_paths,
            "candidate_paths": [item.path for item in files],
        }
        return files, summaries, debug, usage

    def diagnose_complete_project_failure(
        self,
        *,
        prompt: str,
        file_plan: dict[str, Any],
        files: list[FileSpec],
        validation: dict[str, Any],
        gap: dict[str, Any],
    ) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "Diagnose why a complete-project generation response did not satisfy the file plan. "
            "Do not write application code. Return ONLY JSON with keys: reason, missing_files, likely_cause, "
            "retry_strategy, retry_recommended. If the candidate returned only one file while the plan requires "
            "multiple files, say that explicitly and explain that the retry must return every planned file wrapper."
        )
        diagnosis, usage = _complete_json(
            self.provider,
            system,
            {
                "request": prompt,
                "file_plan": _compact_file_plan_for_project(file_plan),
                "planned_paths": gap.get("planned_paths") or [],
                "candidate_paths": gap.get("candidate_paths") or [],
                "candidate_file_summaries": [_file_summary(item, purpose="incomplete complete-project output") for item in files[:12]],
                "missing_paths": gap.get("missing_paths") or [],
                "validation": {
                    "failure_category": validation.get("failure_category"),
                    "failure_reasons": validation.get("failure_reasons") or {},
                    "missing_required": (validation.get("static") or {}).get("missing_required") or [],
                    "missing_artifacts": ((validation.get("artifact_validation") or {}).get("missing_artifacts") or [])[:30],
                },
            },
            max_tokens=1200,
            temperature=0.0,
            trace=self.trace,
            stage="generation.complete_project_diagnosis",
        )
        return diagnosis, usage

    def retry_complete_project(
        self,
        *,
        prompt: str,
        schema_plan: dict[str, Any],
        api_contract: dict[str, Any],
        file_plan: dict[str, Any],
        diagnosis: dict[str, Any],
        gap: dict[str, Any],
    ) -> tuple[list[FileSpec], list[dict[str, Any]], dict[str, Any], ModelRunUsage]:
        required_paths = sorted(set(gap.get("planned_paths") or []) | REQUIRED_PROJECT_FILES)
        system = (
            "Retry the complete FastAPI project generation. Return only file wrappers, no prose, no markdown fences. "
            "You must return one complete wrapper for every path in required_paths. "
            "Do not collapse the project into one file unless required_paths contains only one file. "
            "Keep framework file roles separated and implement real code with no pass/TODO/placeholders. "
            "Use this exact wrapper for each file:\n"
            "### FILE: path/to/file.py ###\n<complete file content>\n### END FILE ###"
        )
        text, usage = _complete_text(
            self.provider,
            system,
            {
                "request": prompt,
                "schema_context": {
                    "domain": schema_plan.get("domain"),
                    "schema": schema_plan.get("schema"),
                    "ddl_sql": schema_plan.get("ddl_sql"),
                },
                "api_context": {
                    "summary": api_contract.get("summary") or api_contract.get("description"),
                    "routes": (api_contract.get("routes") or [])[:80],
                    "artifact_contract": _compact_contract_for_prompt(api_contract.get("artifact_contract") or {}),
                },
                "file_plan": _compact_file_plan_for_project(file_plan),
                "required_paths": required_paths,
                "previous_incomplete_output": {
                    "candidate_paths": gap.get("candidate_paths") or [],
                    "missing_paths": gap.get("missing_paths") or [],
                    "diagnosis": diagnosis,
                },
                "file_role_rules": FILE_ROLE_RULES,
            },
            max_tokens=12000,
            temperature=0.0,
            trace=self.trace,
            stage="generation.complete_project_retry",
        )
        files = _normalize_required_file_names(_parse_marked_file_specs(text))
        summaries = [_file_summary(spec, purpose="complete-project retry candidate") for spec in files]
        debug = {
            "raw_chars": len(text),
            "file_count": len(files),
            "planned_paths": gap.get("planned_paths") or [],
            "candidate_paths": [item.path for item in files],
            "diagnosis": diagnosis,
        }
        return files, summaries, debug, usage

    def repair_generated_files(
        self,
        prompt: str,
        contract: dict[str, Any],
        files: list[FileSpec],
        validation: dict[str, Any],
        *,
        single_file_reference: FileSpec | None = None,
        focused: bool = False,
    ) -> tuple[list[FileSpec], dict[str, Any], ModelRunUsage]:
        if focused:
            system = (
                "Return corrected FastAPI files using only file wrappers. No prose. "
                "Return only the affected changed files; they will be merged into the previous candidate. "
                "Use the contract and validation failures as the source of truth. "
                "Do not add resources, tables, routers, or routes outside contract.canonical_spec. "
                "Treat static file-role errors, unresolved imports, undefined names, placeholder functions, and missing artifacts as blockers. "
                f"If one of {FASTAPI_PROFILE.required_files_text} is affected, include that complete file. "
                "Implement every missing table, field, route, filter, relationship, JWT/auth behavior, "
                "and validation artifact listed in validation. "
                "Do not put route handlers in database files. Do not instantiate FastAPI outside the entrypoint. "
                "Do not use Pydantic schemas as substitutes for persistence models when database tables are required. "
                "Use this wrapper for each file:\n### FILE: path/to/file.py ###\n<complete file content>\n### END FILE ###"
            )
        else:
            system = (
                "You are repairing a generated FastAPI backend that failed artifact validation. "
                "Return a corrected complete project using only file wrappers. Do not output prose. "
                "Treat static file-role errors, unresolved imports, undefined names, placeholder functions, and missing artifacts as blockers. "
                "Implement the missing routes, SQLModel tables, fields, query filters, relationships, JWT auth, "
                "and auth protection described by validation.artifact_validation. "
                f"You may keep the implementation compact in {FASTAPI_PROFILE.entrypoint_file} if that is the most reliable fix. "
                f"Include {FASTAPI_PROFILE.required_files_text}. "
                "Do not put route handlers in database files. Do not instantiate FastAPI outside the entrypoint. "
                "Do not use Pydantic schemas as substitutes for persistence models when database tables are required. "
                "Use this wrapper for each file:\n### FILE: path/to/file.py ###\n<complete file content>\n### END FILE ###"
            )
        remaining_chars = 12_000
        compact_files = [
            {"path": item.path, "content": item.content[: max(1_000, remaining_chars // max(1, len(files)))]}
            for item in files
        ]
        validation_compact = {
            "static": validation.get("static") or {},
            "artifact_validation": {
                "missing_artifacts": ((validation.get("artifact_validation") or {}).get("missing_artifacts") or [])[:30],
                "regressions": ((validation.get("artifact_validation") or {}).get("regressions") or [])[:20],
                "warnings": ((validation.get("artifact_validation") or {}).get("warnings") or [])[:10],
                "detected_artifacts": (validation.get("artifact_validation") or {}).get("detected_artifacts") or {},
            },
        }
        repair_plan = _validation_repair_plan(validation, contract.get("canonical_spec") or {})
        text, usage = _complete_text(
            self.provider,
            system,
            {
                "request": prompt,
                "contract": contract,
                "canonical_spec": contract.get("canonical_spec") or {},
                "allowed_resources": (contract.get("canonical_spec") or {}).get("allowed_resources") or [],
                "current_files": compact_files,
                "candidate_file_paths": [item.path for item in files],
                "validation_fix_plan": repair_plan,
                "single_file_reference": (
                    {"path": single_file_reference.path, "content": single_file_reference.content[:12_000]}
                    if single_file_reference and not focused
                    else None
                ),
                "validation": validation_compact,
                "file_role_rules": FILE_ROLE_RULES,
                "role_instructions": {
                    path: _role_instruction(path)
                    for path in [item.path for item in files]
                },
            },
            max_tokens=8000,
            trace=self.trace,
            stage="generation.repair_focused" if focused else "generation.repair",
        )
        repaired = _normalize_required_file_names(_parse_marked_file_specs(text))
        if focused:
            merged = _merge_repaired_file_specs(files, repaired)
            return merged, {"raw_chars": len(text), "file_count": len(repaired), "targeted": True, "repair_plan": repair_plan}, usage
        return repaired, {"raw_chars": len(text), "file_count": len(repaired), "repair_plan": repair_plan}, usage

    def build(self, prompt: str, *, existing_context: str = "", model_first: bool = False) -> ModelBuildResult:
        self.trace = []
        usage = ModelRunUsage()
        schema_plan, schema_usage = self.plan_schema(prompt, existing_context=existing_context)
        usage.add_usage(schema_usage)
        prompt_intent = extract_prompt_intent(prompt)
        schema_plan = merge_intent_into_schema_plan(schema_plan, prompt_intent)
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.prompt_intent",
                "status": "ok",
                "contract": prompt_intent.get("contract") or {},
                "schema_additions": prompt_intent.get("schema_additions") or {},
                "custom_routes": prompt_intent.get("custom_routes") or [],
                "auth_policy": prompt_intent.get("auth_policy") or {},
            },
        )
        api_contract = _deterministic_api_contract(prompt, schema_plan, prompt_intent)
        if api_contract is None:
            api_contract, api_usage = self.plan_api_contract(prompt, schema_plan)
            usage.add_usage(api_usage)
            api_contract = merge_intent_into_api_contract(api_contract, prompt_intent)
        else:
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.api_contract",
                    "status": "deterministic",
                    "provider": provider_label(self.provider, suffix="pipeline"),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "parsed_keys": sorted(api_contract.keys()),
                    "reason": "prompt_intent_schema_fast_path",
                },
            )
        schema_plan, api_contract, allowlist_debug = apply_resource_allowlist(
            prompt=prompt,
            schema_plan=schema_plan,
            api_contract=api_contract,
        )
        canonical_spec = build_canonical_project_spec(
            prompt=prompt,
            schema_plan=schema_plan,
            api_contract=api_contract,
            prompt_intent=prompt_intent,
        )
        canonical_spec["drift_rejections"] = allowlist_debug.get("rejections") or []
        schema_plan = enrich_schema_plan_from_canonical_spec(schema_plan, canonical_spec)
        api_contract["artifact_contract"] = merge_canonical_spec_into_contract(
            api_contract["artifact_contract"],
            canonical_spec,
        )
        api_contract["canonical_spec"] = canonical_spec
        quests = build_generation_quests(canonical_spec, api_contract["artifact_contract"])
        renderer_coverage = _renderer_coverage(canonical_spec, api_contract)
        capability_plan = renderer_coverage.get("capability_plan") or build_capability_plan(canonical_spec, api_contract.get("artifact_contract") or {})
        pipeline_audit = build_pipeline_audit(
            accepted=False,
            capability_plan=capability_plan,
            quest_results=None,
            validation=None,
            usage=usage,
            changed_files=[],
        )
        if (not model_first) and "ollama" not in self.provider.__class__.__name__.lower() and hasattr(self.provider, "responses") and renderer_coverage["covered"]:
            file_plan = _renderer_file_plan(schema_plan, api_contract, canonical_spec)
            generation_strategy = "renderer_fast_path"
        else:
            file_plan, file_plan_usage = self.plan_files(prompt, schema_plan, api_contract)
            usage.add_usage(file_plan_usage)
            generation_strategy = "model_codegen_incremental"
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.canonical_spec",
                "status": "ok",
                "summary": canonical_spec.get("summary") or {},
                "allowed_resources": canonical_spec.get("allowed_resources") or [],
                "drift_rejections": canonical_spec.get("drift_rejections") or [],
                "renderer_coverage": renderer_coverage,
            },
        )
        contract = {
            "project_name": api_contract.get("project_name") or schema_plan.get("domain") or "generated_project",
            "description": api_contract.get("summary") or "",
            "requirements": api_contract["requirements"],
            "artifact_contract": api_contract["artifact_contract"],
            "canonical_spec": canonical_spec,
            "assumptions": [
                *(schema_plan.get("assumptions") or []),
                *(api_contract.get("assumptions") or []),
            ],
        }
        rendered_project = render_fastapi_sqlmodel_project(
            prompt=prompt,
            schema_plan=schema_plan,
            api_contract=api_contract,
            file_plan=file_plan,
        )
        if rendered_project is not None and renderer_coverage["covered"]:
            quest_results = execute_rendered_quests(
                rendered_project.files,
                quests,
                contract["artifact_contract"],
            )
            rendered_validation = quest_results["final_validation"]
            repair_plan = {} if rendered_validation["accepted"] else _validation_repair_plan(rendered_validation, canonical_spec)
            run_report = build_quest_run_report(
                quests=quests,
                quest_results=quest_results,
                usage=usage,
                changed_files=[item.path for item in rendered_project.files],
            )
            pipeline_audit = build_pipeline_audit(
                accepted=bool(rendered_validation.get("accepted")),
                capability_plan=capability_plan,
                quest_results=quest_results,
                validation=rendered_validation,
                usage=usage,
                changed_files=[item.path for item in rendered_project.files],
            )
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.validation.spec_renderer",
                    "status": "accepted" if rendered_validation["accepted"] else "rejected",
                    "candidate_files": [item.path for item in rendered_project.files],
                    "static_checks": (rendered_validation["static"].get("checks") or [])[:20],
                    "missing_required": (rendered_validation["static"].get("missing_required") or [])[:20],
                    "missing_artifacts": (rendered_validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                    "regressions": (rendered_validation["artifact_validation"].get("regressions") or [])[:20],
                    "failure_category": rendered_validation.get("failure_category"),
                    "render_plan": rendered_project.render_plan,
                    "repair_plan": repair_plan,
                    "completed_quests": [item["id"] for item in quest_results["completed"]],
                    "failed_quests": [item["id"] for item in quest_results["failed"]],
                },
            )
            if rendered_validation["accepted"]:
                return ModelBuildResult(
                    plan={
                        "intent": "model_spec_rendered_generation",
                        "project_name": contract.get("project_name") or "generated_project",
                        "description": contract.get("description") or "",
                        "requirements": contract["requirements"],
                        "file_tree": [item.path for item in rendered_project.files],
                        "assumptions": contract.get("assumptions") or [],
                        "artifact_contract": contract["artifact_contract"],
                        "canonical_spec": canonical_spec,
                        "schema_plan": schema_plan,
                        "api_contract": api_contract,
                        "file_plan": file_plan,
                        "render_plan": rendered_project.render_plan,
                        "quests": quest_dicts(quests),
                        "run_report": run_report,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                    },
                    files=rendered_project.files,
                    requirements=contract["requirements"],
                    artifact_contract=contract["artifact_contract"],
                    provider=provider_label(self.provider, suffix="generation"),
                    usage=usage,
                    stage_outputs={
                        "generation_strategy": generation_strategy,
                        "schema_plan": schema_plan,
                        "canonical_spec": canonical_spec,
                        "api_contract": api_contract,
                        "file_plan": file_plan,
                        "render_plan": rendered_project.render_plan,
                        "renderer_coverage": renderer_coverage,
                        "drift_rejections": canonical_spec.get("drift_rejections") or [],
                        "repair_plan": repair_plan,
                        "quests": quest_dicts(quests),
                        "quest_results": quest_results,
                        "run_report": run_report,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                        "file_summaries": [_file_summary(item) for item in rendered_project.files],
                        "validation": rendered_validation,
                        "trace": self.trace,
                    },
                )
            return ModelBuildResult(
                plan={
                    "intent": "model_spec_rendered_generation",
                    "project_name": contract.get("project_name") or "generated_project",
                    "description": contract.get("description") or "",
                    "requirements": contract["requirements"],
                    "file_tree": [item.path for item in rendered_project.files],
                    "assumptions": contract.get("assumptions") or [],
                    "artifact_contract": contract["artifact_contract"],
                    "canonical_spec": canonical_spec,
                    "schema_plan": schema_plan,
                    "api_contract": api_contract,
                    "file_plan": file_plan,
                    "render_plan": rendered_project.render_plan,
                    "repair_plan": repair_plan,
                    "quests": quest_dicts(quests),
                    "run_report": run_report,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                },
                files=rendered_project.files,
                requirements=contract["requirements"],
                artifact_contract=contract["artifact_contract"],
                provider=provider_label(self.provider, suffix="generation"),
                usage=usage,
                stage_outputs={
                    "generation_strategy": "renderer_fast_path_rejected",
                    "schema_plan": schema_plan,
                    "canonical_spec": canonical_spec,
                    "api_contract": api_contract,
                    "file_plan": file_plan,
                    "render_plan": rendered_project.render_plan,
                    "renderer_coverage": renderer_coverage,
                    "drift_rejections": canonical_spec.get("drift_rejections") or [],
                    "repair_plan": repair_plan,
                    "quests": quest_dicts(quests),
                    "quest_results": quest_results,
                    "run_report": run_report,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                    "file_summaries": [_file_summary(item) for item in rendered_project.files],
                    "validation": rendered_validation,
                    "trace": self.trace,
                },
            )
        single_file = None
        repair_debug: list[dict[str, Any]] = []
        complete_project_debug: list[dict[str, Any]] = []
        files: list[FileSpec] = []
        file_summaries: list[dict[str, Any]] = []
        validation: dict[str, Any] | None = None
        best_files: list[FileSpec] = []
        best_validation: dict[str, Any] | None = None
        complete_project_accepted = False

        if self.should_try_complete_project(file_plan):
            try:
                complete_files, complete_summaries, complete_debug, complete_usage = self.generate_complete_project(
                    prompt=prompt,
                    schema_plan=schema_plan,
                    api_contract=api_contract,
                    file_plan=file_plan,
                )
                usage.add_usage(complete_usage)
                complete_validation = _validate_file_specs(complete_files, contract["artifact_contract"])
                complete_gap = _complete_project_gap(complete_files, file_plan)
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "generation.validation.complete_project",
                        "status": "accepted" if complete_validation["accepted"] else "rejected",
                        "candidate_files": [item.path for item in complete_files],
                        "missing_required": (complete_validation["static"].get("missing_required") or [])[:20],
                        "missing_artifacts": (complete_validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                        "regressions": (complete_validation["artifact_validation"].get("regressions") or [])[:20],
                        "failure_category": complete_validation.get("failure_category"),
                        "missing_paths": complete_gap.get("missing_paths") or [],
                    },
                )
                complete_project_debug.append(
                    {
                        **complete_debug,
                        "gap": complete_gap,
                        "accepted": complete_validation["accepted"],
                        "static_safe": complete_validation.get("static_safe"),
                        "failure_category": complete_validation.get("failure_category"),
                        "missing_artifacts": (complete_validation["artifact_validation"].get("missing_artifacts") or [])[:20],
                        "missing_required": (complete_validation["static"].get("missing_required") or [])[:10],
                    }
                )
                best_files = complete_files
                best_validation = complete_validation
                if not complete_validation["accepted"] and not complete_gap["complete"]:
                    diagnosis, diagnosis_usage = self.diagnose_complete_project_failure(
                        prompt=prompt,
                        file_plan=file_plan,
                        files=complete_files,
                        validation=complete_validation,
                        gap=complete_gap,
                    )
                    usage.add_usage(diagnosis_usage)
                    retry_files, retry_summaries, retry_debug, retry_usage = self.retry_complete_project(
                        prompt=prompt,
                        schema_plan=schema_plan,
                        api_contract=api_contract,
                        file_plan=file_plan,
                        diagnosis=diagnosis,
                        gap=complete_gap,
                    )
                    retry_usage.retries += 1
                    usage.add_usage(retry_usage)
                    retry_validation = _validate_file_specs(retry_files, contract["artifact_contract"])
                    retry_gap = _complete_project_gap(retry_files, file_plan)
                    _record_trace_event(
                        self.trace,
                        {
                            "stage": "generation.validation.complete_project_retry",
                            "status": "accepted" if retry_validation["accepted"] else "rejected",
                            "candidate_files": [item.path for item in retry_files],
                            "missing_required": (retry_validation["static"].get("missing_required") or [])[:20],
                            "missing_artifacts": (retry_validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                            "regressions": (retry_validation["artifact_validation"].get("regressions") or [])[:20],
                            "failure_category": retry_validation.get("failure_category"),
                            "missing_paths": retry_gap.get("missing_paths") or [],
                        },
                    )
                    complete_project_debug.append(
                        {
                            **retry_debug,
                            "retry": True,
                            "gap": retry_gap,
                            "accepted": retry_validation["accepted"],
                            "static_safe": retry_validation.get("static_safe"),
                            "failure_category": retry_validation.get("failure_category"),
                            "missing_artifacts": (retry_validation["artifact_validation"].get("missing_artifacts") or [])[:20],
                            "missing_required": (retry_validation["static"].get("missing_required") or [])[:10],
                        }
                    )
                    if _better_candidate(retry_files, retry_validation, best_files, best_validation):
                        best_files = retry_files
                        best_validation = retry_validation
                    complete_files = retry_files
                    complete_summaries = retry_summaries
                    complete_validation = retry_validation
                if complete_validation["accepted"]:
                    files = complete_files
                    file_summaries = complete_summaries
                    validation = complete_validation
                    complete_project_accepted = True
            except ValidationError as exc:
                usage.retries += 1
                complete_project_debug.append({"error": str(exc), "accepted": False})

        if not complete_project_accepted:
            files, file_summaries, file_usage = self.generate_planned_files(
                prompt=prompt,
                schema_plan=schema_plan,
                api_contract=api_contract,
                file_plan=file_plan,
            )
            usage.add_usage(file_usage)
            validation = _validate_file_specs(files, contract["artifact_contract"])
            if best_validation is None or _better_candidate(files, validation, best_files, best_validation):
                best_files = files
                best_validation = validation

        if validation is None:
            validation = _validate_file_specs(files, contract["artifact_contract"])
        if best_validation is None:
            best_files = files
            best_validation = validation
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.validation.initial",
                "status": "accepted" if validation["accepted"] else "rejected",
                "candidate_files": [item.path for item in files],
                "candidate_source": "complete_project" if complete_project_accepted else "per_file",
                "static_checks": (validation["static"].get("checks") or [])[:20],
                "missing_required": (validation["static"].get("missing_required") or [])[:20],
                "missing_artifacts": (validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                "regressions": (validation["artifact_validation"].get("regressions") or [])[:20],
                "failure_category": validation.get("failure_category"),
            },
        )
        if not validation["accepted"]:
            quality_gate = _candidate_quality_gate(files, validation, contract["artifact_contract"])
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.quality_gate",
                    "status": "passed" if quality_gate["passed"] else "blocked",
                    "issues": quality_gate.get("issues") or [],
                    "failure_category": validation.get("failure_category"),
                    "failure_reasons": validation.get("failure_reasons") or {},
                },
            )
            repair_plan = _validation_repair_plan(validation, canonical_spec)
            for focused in (True,):
                try:
                    files, repair_stage_debug, repair_usage = self.repair_generated_files(
                        prompt,
                        contract,
                        files,
                        validation,
                        single_file_reference=single_file,
                        focused=focused,
                    )
                    repair_usage.retries += 1
                    usage.add_usage(repair_usage)
                    repaired_validation = _validate_file_specs(files, contract["artifact_contract"])
                    if _better_candidate(files, repaired_validation, best_files, best_validation):
                        best_files = files
                        best_validation = repaired_validation
                    validation = repaired_validation
                    _record_trace_event(
                        self.trace,
                        {
                            "stage": "generation.validation.repair_focused" if focused else "generation.validation.repair",
                            "status": "accepted" if validation["accepted"] else "rejected",
                            "candidate_files": [item.path for item in files],
                            "static_checks": (validation["static"].get("checks") or [])[:20],
                            "missing_required": (validation["static"].get("missing_required") or [])[:20],
                            "missing_artifacts": (validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                            "regressions": (validation["artifact_validation"].get("regressions") or [])[:20],
                            "failure_category": validation.get("failure_category"),
                            "repair_plan": repair_plan,
                        },
                    )
                    repair_debug.append(
                        {
                            **repair_stage_debug,
                            "mode": "focused_regeneration" if focused else "repair",
                            "accepted": validation["accepted"],
                            "failure_category": validation.get("failure_category"),
                            "repair_plan": repair_plan,
                            "missing_artifacts": (validation["artifact_validation"].get("missing_artifacts") or [])[:20],
                            "missing_required": (validation["static"].get("missing_required") or [])[:10],
                        }
                    )
                    if validation["accepted"]:
                        break
                except ValidationError as exc:
                    usage.retries += 1
                    repair_debug.append(
                        {
                            "mode": "focused_regeneration" if focused else "repair",
                            "error": str(exc),
                            "accepted": False,
                            "missing_artifacts": (validation["artifact_validation"].get("missing_artifacts") or [])[:20],
                            "missing_required": (validation["static"].get("missing_required") or [])[:10],
                        }
                    )
        if best_files is not files or best_validation is not validation:
            files = best_files
            validation = best_validation
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.candidate_selection",
                    "status": "accepted" if validation["accepted"] else "static_safe" if validation.get("static_safe") else "rejected",
                    "candidate_files": [item.path for item in files],
                    "missing_required": (validation["static"].get("missing_required") or [])[:20],
                    "missing_artifacts": (validation["artifact_validation"].get("missing_artifacts") or [])[:30],
                    "regressions": (validation["artifact_validation"].get("regressions") or [])[:20],
                },
            )
        pipeline_audit = build_pipeline_audit(
            accepted=bool((validation or {}).get("accepted")),
            capability_plan=capability_plan,
            quest_results=None,
            validation=validation,
            usage=usage,
            changed_files=[item.path for item in files],
        )
        return ModelBuildResult(
            plan={
                "intent": "model_owned_generation",
                "project_name": contract.get("project_name") or "generated_project",
                "description": contract.get("description") or "",
                "requirements": contract["requirements"],
                "file_tree": [item.get("path") for item in file_plan.get("files") or [] if isinstance(item, dict)],
                "assumptions": contract.get("assumptions") or [],
                "artifact_contract": contract["artifact_contract"],
                "canonical_spec": canonical_spec,
                "schema_plan": schema_plan,
                "api_contract": api_contract,
                "file_plan": file_plan,
                "quests": quest_dicts(quests),
                "capability_plan": capability_plan,
                "pipeline_audit": pipeline_audit,
            },
            files=files,
            requirements=contract["requirements"],
            artifact_contract=contract["artifact_contract"],
            provider=provider_label(self.provider, suffix="generation"),
            usage=usage,
            stage_outputs={
                "generation_strategy": generation_strategy,
                "schema_plan": schema_plan,
                "canonical_spec": canonical_spec,
                "api_contract": api_contract,
                "file_plan": file_plan,
                "file_summaries": file_summaries,
                "complete_project": complete_project_debug,
                "repair": repair_debug,
                "renderer_coverage": renderer_coverage,
                "drift_rejections": canonical_spec.get("drift_rejections") or [],
                "repair_plan": _validation_repair_plan(validation, canonical_spec) if validation else {},
                "quests": quest_dicts(quests),
                "capability_plan": capability_plan,
                "pipeline_audit": pipeline_audit,
                "validation": validation,
                "trace": self.trace,
            },
        )

    def preview(self, prompt: str, *, existing_context: str = "") -> ModelBuildResult:
        self.trace = []
        usage = ModelRunUsage()
        schema_plan, schema_usage = self.plan_schema(prompt, existing_context=existing_context)
        usage.add_usage(schema_usage)
        prompt_intent = extract_prompt_intent(prompt)
        schema_plan = merge_intent_into_schema_plan(schema_plan, prompt_intent)
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.prompt_intent",
                "status": "ok",
                "contract": prompt_intent.get("contract") or {},
                "schema_additions": prompt_intent.get("schema_additions") or {},
                "custom_routes": prompt_intent.get("custom_routes") or [],
                "auth_policy": prompt_intent.get("auth_policy") or {},
            },
        )
        api_contract = _deterministic_api_contract(prompt, schema_plan, prompt_intent)
        if api_contract is None:
            api_contract, api_usage = self.plan_api_contract(prompt, schema_plan)
            usage.add_usage(api_usage)
            api_contract = merge_intent_into_api_contract(api_contract, prompt_intent)
        else:
            _record_trace_event(
                self.trace,
                {
                    "stage": "generation.api_contract",
                    "status": "deterministic",
                    "provider": provider_label(self.provider, suffix="pipeline"),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "parsed_keys": sorted(api_contract.keys()),
                    "reason": "prompt_intent_schema_fast_path",
                },
            )
        schema_plan, api_contract, allowlist_debug = apply_resource_allowlist(
            prompt=prompt,
            schema_plan=schema_plan,
            api_contract=api_contract,
        )
        canonical_spec = build_canonical_project_spec(
            prompt=prompt,
            schema_plan=schema_plan,
            api_contract=api_contract,
            prompt_intent=prompt_intent,
        )
        canonical_spec["drift_rejections"] = allowlist_debug.get("rejections") or []
        schema_plan = enrich_schema_plan_from_canonical_spec(schema_plan, canonical_spec)
        api_contract["artifact_contract"] = merge_canonical_spec_into_contract(
            api_contract["artifact_contract"],
            canonical_spec,
        )
        api_contract["canonical_spec"] = canonical_spec
        renderer_coverage = _renderer_coverage(canonical_spec, api_contract)
        capability_plan = renderer_coverage.get("capability_plan") or build_capability_plan(canonical_spec, api_contract.get("artifact_contract") or {})
        pipeline_audit = build_pipeline_audit(
            accepted=False,
            capability_plan=capability_plan,
            quest_results=None,
            validation=None,
            usage=usage,
            changed_files=[],
        )
        if hasattr(self.provider, "responses") and renderer_coverage["covered"]:
            file_plan = _renderer_file_plan(schema_plan, api_contract, canonical_spec)
            generation_strategy = "renderer_fast_path"
        else:
            file_plan, file_plan_usage = self.plan_files(prompt, schema_plan, api_contract)
            usage.add_usage(file_plan_usage)
            generation_strategy = "model_codegen_incremental"
        _record_trace_event(
            self.trace,
            {
                "stage": "generation.canonical_spec",
                "status": "ok",
                "summary": canonical_spec.get("summary") or {},
                "allowed_resources": canonical_spec.get("allowed_resources") or [],
                "drift_rejections": canonical_spec.get("drift_rejections") or [],
                "renderer_coverage": renderer_coverage,
            },
        )
        paths = [
            FileSpec(path=str(item.get("path")), content="")
            for item in file_plan.get("files") or []
            if isinstance(item, dict) and item.get("path")
        ]
        return ModelBuildResult(
            plan={
                "intent": "model_owned_generation_preview",
                "project_name": api_contract.get("project_name") or schema_plan.get("domain") or "generated_project",
                "description": api_contract.get("summary") or api_contract.get("description") or "",
                "requirements": api_contract["requirements"],
                "file_tree": [item.path for item in paths],
                "assumptions": [
                    *(schema_plan.get("assumptions") or []),
                    *(api_contract.get("assumptions") or []),
                ],
                "artifact_contract": api_contract["artifact_contract"],
                "canonical_spec": canonical_spec,
                "schema_plan": schema_plan,
                "api_contract": api_contract,
                "file_plan": file_plan,
            },
            files=paths,
            requirements=api_contract["requirements"],
            artifact_contract=api_contract["artifact_contract"],
            provider=provider_label(self.provider, suffix="generation"),
            usage=usage,
            stage_outputs={
                "generation_strategy": generation_strategy,
                "schema_plan": schema_plan,
                "canonical_spec": canonical_spec,
                "api_contract": api_contract,
                "file_plan": file_plan,
                "renderer_coverage": renderer_coverage,
                "pipeline_audit": pipeline_audit,
                "drift_rejections": canonical_spec.get("drift_rejections") or [],
                "trace": self.trace,
            },
        )
