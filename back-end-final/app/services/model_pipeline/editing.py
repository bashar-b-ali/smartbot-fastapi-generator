from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.exceptions import ValidationError
from app.llm.file_spec import FileSpec
from app.llm.project_validator import validate_written_project
from app.llm.writer import WriteOutcome
from app.services import model_project_pipeline as _common
from app.services.model_pipeline.capabilities import (
    build_capability_plan,
    build_pipeline_audit,
)
from app.services.model_pipeline.intent import (
    extract_prompt_intent,
    merge_intent_into_api_contract,
    merge_intent_into_schema_plan,
    user_request_text,
)
from app.services.model_pipeline.llm_io import (
    complete_json as _complete_json,
)
from app.services.model_pipeline.llm_io import (
    complete_json_with_repair as _complete_json_with_repair,
)
from app.services.model_pipeline.llm_io import (
    provider_label,
)
from app.services.model_pipeline.llm_io import (
    record_trace_event as _record_trace_event,
)
from app.services.model_pipeline.patch_ast import (
    append_function,
    insert_class_field,
    insert_import,
    insert_router_wiring,
)
from app.services.model_pipeline.paths import (
    assert_not_placeholder as _assert_not_placeholder,
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
    build_canonical_edit_spec,
    enrich_schema_plan_from_canonical_spec,
    merge_canonical_spec_into_contract,
)
from app.services.model_pipeline.types import (
    ModelEditResult,
    ModelJsonResponseError,
    ModelPatchValidationError,
    ModelRunUsage,
)
from app.services.project_edit.artifact_validation import validate_artifacts
from app.services.project_edit.io import (
    ensure_inside_root,
    safe_rel,
    write_text_if_changed,
)
from app.services.project_edit.selection import select_edit_files
from app.services.project_indexer import build_project_index
from app.services.runtime_validation import validate_runtime_project

ARTIFACT_KEYS = _common.ARTIFACT_KEYS
MAX_VALIDATION_REPAIR_TASKS = 2
# Code-writing stages get few files with intact source instead of many files
# the context budget would shred into unanchorable fragments.
MAX_CODEGEN_CONTEXT_FILES = 3
MAX_EDIT_FILE_CONTEXT_CHARS = _common.MAX_EDIT_FILE_CONTEXT_CHARS
MAX_FULL_FILE_EDIT_CONTEXT_CHARS = _common.MAX_FULL_FILE_EDIT_CONTEXT_CHARS
MAX_PATCH_FILE_CONTEXT_CHARS = _common.MAX_PATCH_FILE_CONTEXT_CHARS
_coerce_artifact_contract = _common._coerce_artifact_contract
_compact_code_context = _common._compact_code_context
_compact_contract_for_prompt = _common._compact_contract_for_prompt
_compact_edit_index = _common._compact_edit_index
_compact_file = _common._compact_file
_compact_file_for_patch = _common._compact_file_for_patch
_compact_previous_response = _common._compact_previous_response
_contract_delta_for_edit = _common._contract_delta_for_edit
_contract_from_data = _common._contract_from_data
_finalize_contract_data = _common._finalize_contract_data
_normalize_file_specs = _common._normalize_file_specs
_requirements = _common._requirements
_validate_schema_plan = _common._validate_schema_plan

def _copy_project_to_temp(root: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="fastapi_edit_"))
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache")
    for item in root.iterdir():
        target = temp_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=ignore)
        elif item.is_file():
            target.write_bytes(item.read_bytes())
    return temp_dir


def _existing_patch_path(root: Path, path: str) -> str:
    target = ensure_inside_root(root, path)
    if target.is_file():
        return path
    parts = Path(path).parts
    if (
        len(parts) >= 3
        and parts[-1] in {"router.py", "routes.py"}
        and parts[-3] in {"routers", "routes"}
    ):
        alternative = Path(*parts[:-2], f"{parts[-2]}.py").as_posix()
        if ensure_inside_root(root, alternative).is_file():
            return alternative
    return path


def _apply_patch_op(root: Path, op: dict[str, Any]) -> WriteOutcome | None:
    operation = str(op.get("op") or op.get("type") or "").strip().lower()
    path = safe_rel(str(op.get("path") or ""))
    if not _is_safe_file_path(path):
        raise ValidationError(f"Unsafe patch path: {path}")
    if path == FASTAPI_PROFILE.endpoint_doc_file:
        return None

    if operation == "create_file":
        content = str(op.get("content") or "")
        if not content.strip():
            raise ValidationError(f"create_file for `{path}` has empty content")
        _assert_not_placeholder(content, label=f"patch `{path}`")
        return write_text_if_changed(root, path, content)

    path = _existing_patch_path(root, path)
    target = ensure_inside_root(root, path)
    if not target.exists() or not target.is_file():
        raise ValidationError(f"Patch target does not exist: {path}")
    text = target.read_text(encoding="utf-8")

    if operation == "insert_import":
        statement = str(op.get("statement") or op.get("content") or "")
        return write_text_if_changed(root, path, insert_import(text, statement))

    if operation == "insert_class_field":
        class_name = str(op.get("class_name") or op.get("symbol") or "")
        content = str(op.get("content") or op.get("field") or "")
        _assert_not_placeholder(content, label=f"patch `{path}`")
        return write_text_if_changed(root, path, insert_class_field(text, class_name, content))

    if operation == "insert_router_wiring":
        import_statement = str(op.get("import_statement") or op.get("import") or "")
        include_statement = str(op.get("include_statement") or op.get("include") or op.get("content") or "")
        _assert_not_placeholder(import_statement, label=f"patch `{path}` import")
        _assert_not_placeholder(include_statement, label=f"patch `{path}` include")
        return write_text_if_changed(root, path, insert_router_wiring(text, import_statement, include_statement))

    if operation == "append_function":
        content = str(op.get("content") or "")
        _assert_not_placeholder(content, label=f"patch `{path}`")
        return write_text_if_changed(root, path, append_function(text, content))

    if operation in {"replace_text", "delete_text"}:
        old = str(op.get("old") or op.get("old_text") or "")
        if not old:
            raise ValidationError(f"{operation} for `{path}` is missing old text")
        count = text.count(old)
        if count != 1:
            raise ValidationError(f"{operation} anchor in `{path}` matched {count} times")
        new = "" if operation == "delete_text" else str(op.get("new") or op.get("new_text") or "")
        _assert_not_placeholder(new, label=f"patch `{path}`")
        return write_text_if_changed(root, path, text.replace(old, new, 1))

    if operation in {"insert_before", "insert_after"}:
        anchor = str(op.get("anchor") or "")
        content = str(op.get("content") or "")
        if not anchor or not content:
            raise ValidationError(f"{operation} for `{path}` needs anchor and content")
        count = text.count(anchor)
        if count != 1:
            raise ValidationError(f"{operation} anchor in `{path}` matched {count} times")
        _assert_not_placeholder(content, label=f"patch `{path}`")
        replacement = f"{content}{anchor}" if operation == "insert_before" else f"{anchor}{content}"
        return write_text_if_changed(root, path, text.replace(anchor, replacement, 1))

    raise ValidationError(f"Unsupported patch operation: {operation}")


def apply_patch_set(root: Path, patch_set: dict[str, Any]) -> list[WriteOutcome]:
    raw_ops = patch_set.get("patches") or patch_set.get("operations") or []
    if not isinstance(raw_ops, list) or not raw_ops:
        raise ValidationError("model patch set must include non-empty patches")
    changed: list[WriteOutcome] = []
    for op in raw_ops:
        if not isinstance(op, dict):
            raise ValidationError("patch operation must be an object")
        outcome = _apply_patch_op(root, op)
        if outcome:
            changed.append(outcome)
    if not changed:
        raise ValidationError("patch set applied no source changes")
    return changed


def _edit_unit_rank(path: str) -> tuple[int, str]:
    normalized = path.replace("\\", "/").lower()
    name = Path(normalized).name
    if name in {"database.py", "db.py", "session.py"}:
        return 0, normalized
    if "model" in name:
        return 1, normalized
    if "schema" in name:
        return 2, normalized
    if any(part in normalized for part in ("repositories/", "services/", "dependencies", "auth")):
        return 3, normalized
    if "routers/" in normalized or "routes/" in normalized:
        return 4, normalized
    if name in {"main.py", "app.py"}:
        return 5, normalized
    if "test" in normalized:
        return 6, normalized
    return 7, normalized


def _codegen_files(selected: list[str], *, limit: int = MAX_CODEGEN_CONTEXT_FILES) -> list[str]:
    """Narrow a selection to the files a single code call can carry intact.

    Selection is already ordered most-relevant first; the entrypoint is kept
    whenever it was selected because router wiring lives there.
    """
    if len(selected) <= limit:
        return list(selected)
    entrypoint = FASTAPI_PROFILE.entrypoint_file
    keeps_entrypoint = entrypoint in selected
    head_limit = limit - 1 if keeps_entrypoint else limit
    narrowed = [path for path in selected if path != entrypoint][:head_limit]
    if keeps_entrypoint:
        narrowed.append(entrypoint)
    return narrowed


def _candidate_edit_units(
    patch_set: dict[str, Any],
    patch_files: list[FileSpec] | None,
) -> list[dict[str, Any]]:
    if patch_files is not None:
        return [
            {"path": spec.path, "files": [spec], "patches": []}
            for spec in sorted(patch_files, key=lambda item: _edit_unit_rank(item.path))
        ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in patch_set.get("patches") or patch_set.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        path = safe_rel(str(operation.get("path") or ""))
        grouped.setdefault(path, []).append(operation)
    return [
        {"path": path, "files": [], "patches": grouped[path]}
        for path in sorted(grouped, key=_edit_unit_rank)
    ]


def _apply_candidate_unit(root: Path, unit: dict[str, Any]) -> list[WriteOutcome]:
    files = unit.get("files") or []
    if files:
        return apply_file_specs(root, files)
    return apply_patch_set(root, {"patches": unit.get("patches") or []})


def _has_artifact_checks(contract: dict[str, Any] | None) -> bool:
    return bool(contract and any(contract.get(key) for key in ARTIFACT_KEYS))


def _with_contract_defaults(data: dict[str, Any], fallback_contract: dict[str, Any] | None) -> dict[str, Any]:
    if not fallback_contract:
        return data
    merged = dict(data)
    if not merged.get("requirements") or _looks_like_dependency_requirements(merged.get("requirements")):
        merged["requirements"] = fallback_contract.get("requirements") or []
    if not _has_artifact_checks(_coerce_artifact_contract(merged.get("artifact_contract"))):
        merged["artifact_contract"] = fallback_contract
    return merged


def _schema_delta_artifacts(schema_delta: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if schema_delta.get("no_schema_change"):
        return {}
    schema = schema_delta.get("schema") if isinstance(schema_delta.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    artifacts: dict[str, list[dict[str, Any]]] = {
        "required_tables": [],
        "required_fields": [],
        "required_filters": [],
        "required_relationships": [],
    }
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or table.get("table") or table.get("db_table_name") or "").strip()
        if not table_name:
            continue
        artifacts["required_tables"].append({"table": table_name})
        for field in table.get("columns") or table.get("fields") or []:
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or field.get("field") or field.get("column") or "").strip()
            if not field_name:
                continue
            artifacts["required_fields"].append({"table": table_name, "field": field_name})
            reference = field.get("references") or field.get("foreign_key")
            if reference:
                target = str(reference).split("(", 1)[0].split(".", 1)[0].strip()
                if target:
                    artifacts["required_relationships"].append(
                        {"from_table": table_name, "field": field_name, "to_table": target}
                    )
            if any(field.get(key) for key in ("filter", "filterable", "searchable")):
                artifacts["required_filters"].append({"table": table_name, "field": field_name})
        for field_name in table.get("filters") or table.get("search_fields") or table.get("filter_fields") or []:
            if isinstance(field_name, dict):
                field_name = field_name.get("field") or field_name.get("name") or field_name.get("column")
            if str(field_name or "").strip():
                artifacts["required_filters"].append({"table": table_name, "field": str(field_name).strip()})
    return {key: value for key, value in artifacts.items() if value}


def _merge_artifact_lists(contract: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    merged = dict(contract)
    for key, values in additions.items():
        if isinstance(values, dict):
            merged[key] = {
                **(merged.get(key) if isinstance(merged.get(key), dict) else {}),
                **values,
            }
            continue
        if not isinstance(values, list):
            merged[key] = values
            continue
        existing = [item for item in merged.get(key) or [] if isinstance(item, dict)]
        seen = {json.dumps(item, sort_keys=True, default=str) for item in existing}
        for value in values:
            marker = json.dumps(value, sort_keys=True, default=str)
            if marker not in seen:
                existing.append(value)
                seen.add(marker)
        merged[key] = existing
    return merged


def _route_mentions_table(route: dict[str, Any], table_name: str) -> bool:
    haystack = " ".join(
        str(route.get(key) or "")
        for key in ("path", "name", "function", "summary", "description", "resource", "table")
    )
    return _name_mentioned(haystack, table_name)


def _validate_api_delta_against_schema(contract: dict[str, Any], schema_delta: dict[str, Any]) -> list[str]:
    schema_artifacts = _schema_delta_artifacts(schema_delta)
    required_tables = [
        str(item.get("table") or item.get("name") or "").strip().lower()
        for item in schema_artifacts.get("required_tables") or []
        if isinstance(item, dict) and str(item.get("table") or item.get("name") or "").strip()
    ]
    if not required_tables:
        return []
    routes = [route for route in contract.get("required_routes") or contract.get("routes") or [] if isinstance(route, dict)]
    errors: list[str] = []
    for table_name in required_tables:
        if routes and not any(_route_mentions_table(route, table_name) for route in routes):
            errors.append(f"API delta routes do not target schema table `{table_name}`")
    return errors


def _schema_table_names(data: dict[str, Any]) -> set[str]:
    schema = data.get("schema") if isinstance(data.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    names: set[str] = set()
    for table in tables:
        if not isinstance(table, dict):
            continue
        for key in ("name", "table", "db_table_name"):
            value = str(table.get(key) or "").strip()
            if value:
                names.add(value.lower())
    return names


def _existing_schema_table_names(index: dict[str, Any] | None) -> set[str]:
    if not index:
        return set()
    database_schema = index.get("database_schema") if isinstance(index.get("database_schema"), dict) else {}
    tables = database_schema.get("tables") if isinstance(database_schema.get("tables"), list) else []
    names: set[str] = set()
    for table in tables:
        if not isinstance(table, dict):
            continue
        for key in ("name", "table", "db_table_name"):
            value = str(table.get(key) or "").strip()
            if value:
                names.add(value.lower())
    return names


def _name_mentioned(prompt: str, name: str) -> bool:
    prompt = user_request_text(prompt)
    normalized_prompt = f" {re.sub(r'[^a-z0-9]+', ' ', prompt.lower().replace('_', ' '))} "
    candidates = {name.lower(), name.lower().replace("_", " ")}
    for candidate in list(candidates):
        if candidate.endswith("s"):
            candidates.add(candidate[:-1])
        else:
            candidates.add(f"{candidate}s")
    return any(f" {candidate} " in normalized_prompt for candidate in candidates if candidate)


def _validate_schema_delta(
    data: dict[str, Any],
    *,
    prompt: str = "",
    index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if data.get("no_schema_change"):
        schema = data.get("schema") if isinstance(data.get("schema"), dict) else {}
        if data.get("affected_tables") or data.get("affected_fields") or schema.get("tables"):
            return {
                "passed": False,
                "errors": ["no_schema_change=true cannot include affected tables, affected fields, or schema.tables"],
                "warnings": [],
                "tables": [],
            }
        return {"passed": True, "errors": [], "warnings": [], "tables": []}
    validation = _validate_schema_plan(data)
    if validation["passed"] and prompt and index:
        stale_existing = sorted(
            table
            for table in (_schema_table_names(data) & _existing_schema_table_names(index))
            if not _name_mentioned(prompt, table)
        )
        if stale_existing:
            validation = {
                **validation,
                "passed": False,
                "errors": [
                    *(validation.get("errors") or []),
                    f"Schema delta copied existing tables not mentioned in request: {', '.join(stale_existing)}",
                ],
        }
    return validation


def _schema_delta_no_change_contradiction(validation: dict[str, Any]) -> bool:
    return any(
        "no_schema_change=true cannot include affected tables" in str(error)
        for error in (validation.get("errors") or [])
    )


def _schema_delta_ddl_from_tables(schema: dict[str, Any]) -> list[str]:
    statements: list[str] = []
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_name = _sql_identifier(table.get("name") or table.get("table") or table.get("db_table_name"))
        if not table_name:
            continue
        columns: list[str] = []
        raw_columns = table.get("columns") or table.get("fields") or []
        for column in raw_columns:
            if not isinstance(column, dict):
                column_name = _sql_identifier(column)
                raw_type = ""
            else:
                column_name = _sql_identifier(column.get("name") or column.get("field") or column.get("column"))
                raw_type = str(column.get("type") or column.get("kind") or "").strip()
            if not column_name:
                continue
            sql_type = _sql_type(raw_type)
            suffix = " PRIMARY KEY" if column_name == "id" else ""
            columns.append(f"{column_name} {sql_type}{suffix}")
        if not columns:
            columns.append("id INTEGER PRIMARY KEY")
        statements.append(f"CREATE TABLE {table_name} ({', '.join(columns)});")
    return statements


def _sql_identifier(value: Any) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if text and text[0].isdigit():
        text = f"field_{text}"
    return text


def _sql_type(raw_type: str) -> str:
    text = raw_type.lower()
    if any(term in text for term in ("int", "serial", "bigint", "smallint")):
        return "INTEGER"
    if any(term in text for term in ("float", "double", "decimal", "numeric", "real")):
        return "REAL"
    if any(term in text for term in ("bool",)):
        return "BOOLEAN"
    if any(term in text for term in ("datetime", "timestamp")):
        return "TIMESTAMP"
    return "TEXT"


def _repair_no_change_contradiction_if_targeted(
    data: dict[str, Any],
    *,
    prompt: str,
    validation: dict[str, Any],
) -> dict[str, Any] | None:
    if not data.get("no_schema_change") or not _schema_delta_no_change_contradiction(validation):
        return None
    schema = data.get("schema") if isinstance(data.get("schema"), dict) else {}
    tables = [
        str(table.get("name") or table.get("table") or table.get("db_table_name") or "").strip()
        for table in (schema.get("tables") or [])
        if isinstance(table, dict)
    ]
    affected_tables = [str(item).strip() for item in (data.get("affected_tables") or []) if str(item).strip()]
    named_targets = [*tables, *affected_tables]
    prompt = user_request_text(prompt)
    if named_targets and not any(_name_mentioned(prompt, target) for target in named_targets):
        return None
    repaired = dict(data)
    repaired["no_schema_change"] = False
    if not str(repaired.get("ddl_sql") or "").strip():
        repaired["ddl_sql"] = _schema_delta_ddl_from_tables(schema)
    if not repaired.get("summary"):
        repaired["summary"] = "Schema changes are required by the requested edit."
    return repaired


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
        "jwt",
        "pyjwt",
        "uvicorn",
        "pydantic",
    }
    behavior_terms = {
        "endpoint",
        "route",
        "crud",
        "create",
        "list",
        "detail",
        "update",
        "table",
        "field",
        "filter",
        "auth",
        "authentication",
        "protect",
        "manage",
        "relationship",
        "foreign key",
    }
    if all(str(item.get("kind") or "").strip().lower() in dependency_kinds for item in dict_items):
        return True
    dependency_like = 0
    for item in dict_items:
        description = str(
            item.get("description")
            or item.get("requirement")
            or item.get("title")
            or item.get("name")
            or ""
        ).strip().lower()
        kind = str(item.get("kind") or "").strip().lower()
        if kind in dependency_kinds:
            dependency_like += 1
            continue
        if any(term in description for term in dependency_terms) and not any(term in description for term in behavior_terms):
            dependency_like += 1
    if dependency_like == len(dict_items):
        return True
    if dependency_like >= max(2, len(dict_items) // 2 + 1):
        return True
    return all(
        item.get("name")
        and item.get("version")
        and not any(item.get(key) for key in ("description", "requirement", "title", "acceptance_checks", "checks"))
        for item in dict_items
    )


def _finalize_patch_set(
    data: dict[str, Any],
    *,
    prompt: str,
    fallback_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if data.get("missing_info"):
        raise ValidationError("Model requested missing information: " + "; ".join(map(str, data["missing_info"])))
    raw_ops = data.get("patches") or data.get("operations")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise ValidationError("model patch set must include non-empty patches")
    for op in raw_ops:
        if not isinstance(op, dict):
            raise ValidationError("patch operation must be an object")
        path = safe_rel(str(op.get("path") or ""))
        if not _is_safe_file_path(path):
            raise ValidationError(f"Unsafe patch path: {path}")
    data = _with_contract_defaults(data, fallback_contract)
    requirements = _requirements(data, prompt)
    data["requirements"] = requirements
    data["artifact_contract"] = _contract_from_data(
        data,
        prompt=prompt,
        requirements=requirements,
        source="edit",
    )
    return data


def _normalize_full_file_edit(
    data: dict[str, Any],
    *,
    prompt: str,
    fallback_contract: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[FileSpec]]:
    if data.get("missing_info"):
        raise ValidationError("Model requested missing information: " + "; ".join(map(str, data["missing_info"])))
    data = _with_contract_defaults(data, fallback_contract)
    requirements = _requirements(data, prompt)
    data["requirements"] = requirements
    data["artifact_contract"] = _contract_from_data(
        data,
        prompt=prompt,
        requirements=requirements,
        source="edit",
    )
    raw_files = data.get("files")
    if isinstance(raw_files, dict):
        normalized_raw_files: list[dict[str, Any]] = []
        for path, value in raw_files.items():
            if isinstance(value, dict):
                content = value.get("content") if "content" in value else value.get("code")
            else:
                content = value
            normalized_raw_files.append({"path": str(path), "content": content})
        raw_files = normalized_raw_files
    if not isinstance(raw_files, list) or not raw_files:
        raise ValidationError("model full-file edit must include non-empty files")
    files = _normalize_file_specs([item for item in raw_files if isinstance(item, dict)])
    if not files:
        raise ValidationError("model full-file edit did not include usable files")
    return data, files


def apply_file_specs(root: Path, files: list[FileSpec]) -> list[WriteOutcome]:
    changed: list[WriteOutcome] = []
    for spec in files:
        changed_outcome = write_text_if_changed(root, spec.path, spec.content)
        if changed_outcome:
            changed.append(changed_outcome)
    if not changed:
        raise ValidationError("model file edit applied no source changes")
    return changed


def _validation_error_is_repairable(
    error: ValidationError,
    *,
    static_validation: dict[str, Any],
    artifact_validation: dict[str, Any],
) -> bool:
    message = str(error)
    if "placeholder marker" in message or "Unsafe" in message:
        return False
    if static_validation or artifact_validation:
        return True
    repairable_markers = (
        "anchor",
        "Patch target does not exist",
        "applied no source changes",
        "file edit applied no source changes",
    )
    return any(marker in message for marker in repairable_markers)


def _path_from_static_check(check: dict[str, Any]) -> str:
    return safe_rel(str(check.get("path") or check.get("file") or ""))


def _router_path_from_artifact_text(text: str) -> str:
    match = re.search(r"\b/[a-zA-Z0-9_-]+", text)
    if not match:
        return ""
    resource = match.group(0).strip("/").split("/", 1)[0].replace("-", "_")
    return f"routers/{resource}.py" if resource else ""


def _validation_repair_tasks(
    *,
    selected: list[str],
    static_validation: dict[str, Any],
    artifact_validation: dict[str, Any],
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    selected_set = set(selected)
    for check in static_validation.get("checks") or []:
        if not isinstance(check, dict) or check.get("passed"):
            continue
        path = _path_from_static_check(check)
        files = [path] if path and (not selected_set or path in selected_set) else list(selected)
        tasks.append(
            {
                "kind": "static_validation",
                "objective": f"Fix static validation in {path or 'selected files'} only.",
                "files": files,
                "errors": [check.get("error") or check],
            }
        )
    for path in static_validation.get("missing_required") or []:
        path = safe_rel(str(path))
        tasks.append(
            {
                "kind": "missing_required_file",
                "objective": f"Create or restore required project file {path}.",
                "files": [path],
                "errors": [f"missing required file {path}"],
            }
        )
    missing_by_file: dict[str, list[str]] = {}
    for item in artifact_validation.get("missing_artifacts") or []:
        text = str(item)
        path = _router_path_from_artifact_text(text)
        if "table" in text.lower() or "field" in text.lower() or "relationship" in text.lower():
            path = "models.py"
        if not path:
            path = selected[0] if selected else "main.py"
        missing_by_file.setdefault(path, []).append(text)
    for path, errors in missing_by_file.items():
        files = [path]
        if path.startswith("routers/"):
            files.append("main.py")
        tasks.append(
            {
                "kind": "missing_artifact",
                "objective": f"Implement missing artifacts connected to {path}.",
                "files": [file for file in files if file],
                "errors": errors[:8],
            }
        )
    for item in artifact_validation.get("regressions") or []:
        tasks.append(
            {
                "kind": "regression",
                "objective": "Restore previous accepted behavior without changing unrelated files.",
                "files": list(selected),
                "errors": [str(item)],
            }
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for task in tasks:
        files = tuple(dict.fromkeys(str(path) for path in task.get("files") or [] if str(path).strip()))
        marker = (str(task.get("kind") or ""), files)
        if marker in seen:
            continue
        deduped.append({**task, "files": list(files)})
        seen.add(marker)
    return deduped[:MAX_VALIDATION_REPAIR_TASKS]


def _file_specs_from_outcomes(root: Path, outcomes: list[WriteOutcome]) -> list[FileSpec]:
    specs: list[FileSpec] = []
    seen: set[str] = set()
    for outcome in outcomes:
        path = safe_rel(outcome.path)
        if path in seen:
            continue
        target = root / path
        if target.exists() and target.is_file():
            specs.append(FileSpec(path=path, content=target.read_text(encoding="utf-8")))
            seen.add(path)
    return specs


def _merge_repaired_file_specs(original: list[FileSpec], repaired: list[FileSpec]) -> list[FileSpec]:
    by_path = {item.path: item for item in original}
    order = [item.path for item in original]
    for item in repaired:
        if item.path not in by_path:
            order.append(item.path)
        by_path[item.path] = item
    return [by_path[path] for path in order if path in by_path]


def _renderer_owned_project(root: Path) -> bool:
    models = root / "models.py"
    schemas = root / "schemas.py"
    routers = root / "routers"
    if not models.exists() or not schemas.exists() or not routers.exists():
        return False
    try:
        model_text = models.read_text(encoding="utf-8")
    except OSError:
        return False
    return "SQLModel" in model_text and "table=True" in model_text


def _saved_schema_plan(saved_memory: dict[str, Any] | None) -> dict[str, Any] | None:
    if not saved_memory:
        return None
    schema_memory = saved_memory.get("schema") if isinstance(saved_memory.get("schema"), dict) else {}
    schema = schema_memory.get("schema") if isinstance(schema_memory.get("schema"), dict) else None
    if not schema:
        return None
    return {
        "domain": schema_memory.get("domain") or "edited_project",
        "ddl_sql": schema_memory.get("ddl_sql") or "",
        "schema": schema,
        "validation": {"passed": True},
        "assumptions": [],
    }


def _schema_plan_from_index(index: dict[str, Any]) -> dict[str, Any] | None:
    indexed_tables = (index.get("database_schema") or {}).get("tables") or []
    if not indexed_tables:
        return None
    filter_map: dict[str, set[str]] = {}
    for item in index.get("query_filters") or []:
        table = str(item.get("table") or "").lower()
        field = str(item.get("field") or "")
        if table and field:
            filter_map.setdefault(table, set()).add(field)
    tables: list[dict[str, Any]] = []
    for table in indexed_tables:
        if not isinstance(table, dict):
            continue
        name = str(table.get("db_table_name") or table.get("name") or "").strip()
        if not name:
            continue
        columns: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        for field in table.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            column = {
                key: value
                for key, value in field.items()
                if key in {"name", "type", "nullable", "default", "primary_key", "index", "unique", "foreign_key"}
                and value is not None
            }
            columns.append(column)
            foreign_key = str(field.get("foreign_key") or "")
            if foreign_key:
                relationships.append(
                    {
                        "column": field.get("name"),
                        "related_table": foreign_key.split(".", 1)[0],
                        "related_column": foreign_key.split(".", 1)[1] if "." in foreign_key else "id",
                    }
                )
        table_filters = sorted(
            filter_map.get(str(table.get("name") or "").lower(), set())
            | filter_map.get(name.lower(), set())
        )
        tables.append(
            {
                "name": name,
                "columns": columns,
                "relationships": relationships,
                "filter_fields": table_filters,
            }
        )
    if not tables:
        return None
    return {
        "domain": "indexed_project",
        "ddl_sql": "",
        "schema": {"tables": tables, "no_database_required": False},
        "validation": {"passed": True},
        "assumptions": ["Current project index is authoritative."],
    }


def _saved_api_contract(saved_memory: dict[str, Any] | None) -> dict[str, Any]:
    if not saved_memory:
        return {}
    api_memory = saved_memory.get("api_contract") if isinstance(saved_memory.get("api_contract"), dict) else {}
    contract = api_memory.get("contract") if isinstance(api_memory.get("contract"), dict) else {}
    return dict(contract)


def _saved_file_plan(saved_memory: dict[str, Any] | None) -> dict[str, Any]:
    if not saved_memory:
        return {}
    file_memory = saved_memory.get("file_plan") if isinstance(saved_memory.get("file_plan"), dict) else {}
    plan = file_memory.get("plan") if isinstance(file_memory.get("plan"), dict) else {}
    return dict(plan)


def _column_key(column: Any) -> str:
    if isinstance(column, dict):
        return str(column.get("name") or column.get("field") or column.get("column") or "").strip().lower()
    return str(column or "").strip().lower()


def _table_key(table: dict[str, Any]) -> str:
    return str(table.get("name") or table.get("table") or table.get("db_table_name") or "").strip().lower()


def _merge_table(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in delta.items():
        if key not in {"columns", "fields", "relationships", "indexes", "filters", "search_fields", "filter_fields"}:
            merged[key] = value
    base_columns = [column for column in (base.get("columns") or base.get("fields") or []) if _column_key(column)]
    delta_columns = [column for column in (delta.get("columns") or delta.get("fields") or []) if _column_key(column)]
    column_by_key = {_column_key(column): column for column in base_columns}
    for column in delta_columns:
        column_by_key[_column_key(column)] = column
    if column_by_key:
        merged["columns"] = list(column_by_key.values())
    for list_key in ("relationships", "indexes", "filters", "search_fields", "filter_fields"):
        values: list[Any] = []
        seen: set[str] = set()
        for value in [*(base.get(list_key) or []), *(delta.get(list_key) or [])]:
            marker = json.dumps(value, sort_keys=True, default=str)
            if marker not in seen:
                values.append(value)
                seen.add(marker)
        if values:
            merged[list_key] = values
    return merged


def _merge_schema_delta_for_renderer(
    saved_memory: dict[str, Any] | None,
    schema_delta: dict[str, Any],
    *,
    index: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    base_plan = _schema_plan_from_index(index or {}) or _saved_schema_plan(saved_memory)
    if not base_plan:
        return None
    if schema_delta.get("no_schema_change"):
        return base_plan
    base_schema = base_plan.get("schema") if isinstance(base_plan.get("schema"), dict) else {}
    delta_schema = schema_delta.get("schema") if isinstance(schema_delta.get("schema"), dict) else {}
    base_tables = [table for table in base_schema.get("tables") or [] if isinstance(table, dict) and _table_key(table)]
    delta_tables = [table for table in delta_schema.get("tables") or [] if isinstance(table, dict) and _table_key(table)]
    table_by_key = {_table_key(table): dict(table) for table in base_tables}
    for table in delta_tables:
        key = _table_key(table)
        table_by_key[key] = _merge_table(table_by_key[key], table) if key in table_by_key else dict(table)
    merged_schema = dict(base_schema)
    merged_schema["tables"] = list(table_by_key.values())
    return {
        **base_plan,
        "ddl_sql": schema_delta.get("ddl_sql") or base_plan.get("ddl_sql") or "",
        "schema": merged_schema,
        "validation": {"passed": True},
    }


def _merge_api_delta_for_renderer(saved_memory: dict[str, Any] | None, edit_contract: dict[str, Any]) -> dict[str, Any]:
    base = _saved_api_contract(saved_memory)
    delta_contract = _coerce_artifact_contract(edit_contract.get("artifact_contract")) or _coerce_artifact_contract(edit_contract) or {}
    merged = dict(base)
    merged["summary"] = edit_contract.get("summary") or base.get("summary") or ""
    merged["requirements"] = [*(base.get("requirements") or []), *(edit_contract.get("requirements") or [])]
    merged["routes"] = [*(base.get("routes") or []), *(edit_contract.get("routes") or [])]
    merged["auth"] = edit_contract.get("auth") or base.get("auth") or {}
    merged["auth_policy"] = {
        **(base.get("auth_policy") if isinstance(base.get("auth_policy"), dict) else {}),
        **(edit_contract.get("auth_policy") if isinstance(edit_contract.get("auth_policy"), dict) else {}),
    }
    merged["websocket_events"] = [*(base.get("websocket_events") or []), *(edit_contract.get("websocket_events") or [])]
    merged["artifact_contract"] = _merge_artifact_lists(
        base.get("artifact_contract") if isinstance(base.get("artifact_contract"), dict) else {},
        delta_contract,
    )
    merged["assumptions"] = [*(base.get("assumptions") or []), *(edit_contract.get("assumptions") or [])]
    return merged


def _apply_prompt_intent_to_schema_delta(schema_delta: dict[str, Any], prompt_intent: dict[str, Any]) -> dict[str, Any]:
    updated = merge_intent_into_schema_plan(schema_delta, prompt_intent)
    additions = (prompt_intent.get("schema_additions") or {}).get("tables") or []
    if additions:
        updated["no_schema_change"] = False
        affected_tables = list(updated.get("affected_tables") or [])
        affected_fields = list(updated.get("affected_fields") or [])
        for table in additions:
            table_name = str(table.get("name") or table.get("table") or "")
            if table_name and table_name not in affected_tables:
                affected_tables.append(table_name)
            for column in table.get("columns") or []:
                if not isinstance(column, dict):
                    continue
                field = str(column.get("name") or "")
                if table_name and field and f"{table_name}.{field}" not in affected_fields:
                    affected_fields.append(f"{table_name}.{field}")
        updated["affected_tables"] = affected_tables
        updated["affected_fields"] = affected_fields
        updated["validation"] = {"passed": True, "errors": [], "warnings": [], "tables": affected_tables}
    return updated


def _deterministic_schema_delta_from_intent(
    prompt: str,
    prompt_intent: dict[str, Any],
    *,
    index: dict[str, Any],
) -> dict[str, Any] | None:
    contract = _coerce_artifact_contract(prompt_intent.get("contract")) or {}
    additions = (prompt_intent.get("schema_additions") or {}).get("tables") or []
    if not additions and not _has_artifact_checks(contract):
        return None
    # Avoid the known natural-language false positive where "pending"
    # describes orders rather than naming a Pending table. Other explicit
    # resources continue using the existing deterministic edit path.
    request_text = user_request_text(prompt).lower()
    for addition in additions:
        table_name = str(addition.get("name") or addition.get("table") or "").strip().lower()
        if table_name != "pendings":
            continue
        if not re.search(r"\b(?:table|model|resource|entity)\s+pending\b", request_text):
            return None
    data: dict[str, Any] = {
        "summary": "Prompt-derived schema delta.",
        "no_schema_change": not additions,
        "ddl_sql": [],
        "schema": {"tables": []},
        "affected_tables": [],
        "affected_fields": [],
        "assumptions": ["Derived from literal prompt artifacts without an LLM planning call."],
    }
    if additions:
        data = _apply_prompt_intent_to_schema_delta(data, prompt_intent)
        schema = data.get("schema") if isinstance(data.get("schema"), dict) else {}
        data["ddl_sql"] = _schema_delta_ddl_from_tables(schema)
    validation = _validate_schema_delta(data, prompt=prompt, index=index)
    if not validation.get("passed"):
        return None
    data["validation"] = validation
    return data


def _deterministic_api_delta_from_intent(prompt: str, prompt_intent: dict[str, Any]) -> dict[str, Any] | None:
    contract = _coerce_artifact_contract(prompt_intent.get("contract")) or {}
    if not _has_artifact_checks(contract):
        return None
    try:
        return _finalize_contract_data(_fallback_api_delta_from_intent(prompt, prompt_intent), prompt=prompt, source="edit")
    except ValidationError:
        return None

def _fallback_api_delta_from_intent(prompt: str, prompt_intent: dict[str, Any]) -> dict[str, Any]:
    artifact_contract = dict(prompt_intent.get("contract") or {})
    if prompt_intent.get("custom_routes"):
        artifact_contract["custom_routes"] = [
            dict(item)
            for item in prompt_intent["custom_routes"]
            if isinstance(item, dict)
        ]
    if prompt_intent.get("auth_policy"):
        artifact_contract["auth_policy"] = dict(prompt_intent["auth_policy"])
    return {
        "summary": "Prompt-derived edit API contract.",
        "requirements": [
            {
                "id": "R1",
                "description": prompt,
                "critical": True,
            }
        ],
        "routes": artifact_contract.get("required_routes") or [],
        "auth": {},
        "auth_policy": dict(prompt_intent.get("auth_policy") or {}),
        "websocket_events": [],
        "artifact_contract": artifact_contract,
        "target_file_hints": [],
        "assumptions": ["Derived from literal prompt artifacts after model API delta failure."],
        "missing_info": [],
    }


class ModelPatchEditPipeline:
    def __init__(self, provider: Any):
        self.provider = provider
        self.trace: list[dict[str, Any]] = []

    def try_rendered_edit(
        self,
        *,
        prompt: str,
        root: Path,
        schema_delta: dict[str, Any],
        edit_contract: dict[str, Any],
        previous_contracts: list[dict[str, Any]] | None,
        saved_memory: dict[str, Any] | None,
        usage: ModelRunUsage,
    ) -> ModelEditResult | None:
        if not _renderer_owned_project(root):
            return None
        current_index = build_project_index(root)
        merged_schema = _merge_schema_delta_for_renderer(
            saved_memory,
            schema_delta,
            index=current_index,
        )
        if not merged_schema:
            return None
        merged_api = _merge_api_delta_for_renderer(saved_memory, edit_contract)
        historical_contract: dict[str, Any] = {}
        for previous in previous_contracts or []:
            if not isinstance(previous, dict) or previous.get("accepted") is not True:
                continue
            historical_contract = _merge_artifact_lists(historical_contract, previous)
        merged_api["artifact_contract"] = _merge_artifact_lists(
            historical_contract,
            merged_api.get("artifact_contract")
            if isinstance(merged_api.get("artifact_contract"), dict)
            else {},
        )
        rendered = render_fastapi_sqlmodel_project(
            prompt=prompt,
            schema_plan=merged_schema,
            api_contract=merged_api,
            file_plan=_saved_file_plan(saved_memory),
        )
        if rendered is None:
            return None
        artifact_contract = _coerce_artifact_contract(edit_contract.get("artifact_contract")) or _coerce_artifact_contract(edit_contract) or {}
        capability_plan = build_capability_plan(edit_contract.get("canonical_spec") or {}, artifact_contract)
        quests = build_generation_quests(edit_contract.get("canonical_spec") or {}, artifact_contract)
        quest_results = execute_rendered_quests(rendered.files, quests, artifact_contract)
        temp_root = _copy_project_to_temp(root)
        static_validation: dict[str, Any] = {}
        artifact_validation: dict[str, Any] = {}
        try:
            changed = apply_file_specs(temp_root, rendered.files)
            static_validation = validate_written_project(temp_root, changed).as_dict()
            artifact_validation = validate_artifacts(
                build_project_index(temp_root),
                artifact_contract,
                previous_contracts=previous_contracts or [],
            ).as_dict()
            runtime_validation = validate_runtime_project(temp_root)
            _record_trace_event(
                self.trace,
                {
                    "stage": "edit.validation.spec_renderer",
                    "status": (
                        "accepted"
                        if static_validation.get("passed") and artifact_validation.get("passed") and runtime_validation.get("passed")
                        else "rejected"
                    ),
                    "candidate_files": [item.path for item in changed],
                    "missing_required": (static_validation.get("missing_required") or [])[:20],
                    "missing_artifacts": (artifact_validation.get("missing_artifacts") or [])[:30],
                    "regressions": (artifact_validation.get("regressions") or [])[:20],
                    "runtime_errors": (runtime_validation.get("errors") or [])[:10],
                    "render_plan": rendered.render_plan,
                    "completed_quests": [item["id"] for item in quest_results["completed"]],
                    "failed_quests": [item["id"] for item in quest_results["failed"]],
                },
            )
            if not static_validation.get("passed") or not artifact_validation.get("passed") or not runtime_validation.get("passed") or quest_results["failed"]:
                return None
            active_changed: list[WriteOutcome] = []
            for outcome in changed:
                source = temp_root / outcome.path
                content = source.read_text(encoding="utf-8")
                active = write_text_if_changed(root, outcome.path, content)
                if active:
                    active_changed.append(active)
            run_report = build_quest_run_report(
                quests=quests,
                quest_results=quest_results,
                usage=usage,
                changed_files=[item.path for item in active_changed],
            )
            pipeline_audit = build_pipeline_audit(
                accepted=True,
                capability_plan=capability_plan,
                quest_results=quest_results,
                validation={
                    "static": static_validation,
                    "artifact_validation": artifact_validation,
                    "runtime_validation": runtime_validation,
                    "accepted": True,
                    "static_safe": bool(static_validation.get("passed")),
                },
                usage=usage,
                changed_files=[item.path for item in active_changed],
            )
            accepted_contract = {**artifact_contract, "accepted": True}
            return ModelEditResult(
                changed=active_changed,
                requirements=edit_contract["requirements"],
                artifact_contract=accepted_contract,
                edit_plan={
                    "intent": "model_spec_rendered_edit",
                    "summary": edit_contract.get("summary") or "",
                    "target_files": [item.path for item in rendered.files],
                    "steps": [],
                    "files": [item.path for item in rendered.files],
                    "requirements": edit_contract["requirements"],
                    "artifact_contract": accepted_contract,
                    "canonical_spec": edit_contract.get("canonical_spec") or {},
                    "schema_delta": schema_delta,
                    "api_delta": edit_contract,
                    "render_plan": rendered.render_plan,
                    "quests": quest_dicts(quests),
                    "run_report": run_report,
                    "capability_plan": capability_plan,
                    "pipeline_audit": pipeline_audit,
                },
                provider=provider_label(self.provider, suffix="edit"),
                usage=usage,
                selected_files=[item.path for item in rendered.files],
                stage_outputs={
                    "schema_delta": schema_delta,
                    "canonical_spec": edit_contract.get("canonical_spec") or {},
                    "api_delta": edit_contract,
                    "render_plan": rendered.render_plan,
                    "quests": quest_dicts(quests),
                    "quest_results": quest_results,
                    "run_report": run_report,
                    "capability_plan": capability_plan,
                    "pipeline_audit": pipeline_audit,
                    "validation": {
                        "static": static_validation,
                        "artifact_validation": artifact_validation,
                        "runtime_validation": runtime_validation,
                        "accepted": True,
                    },
                    "trace": self.trace,
                },
            )
        except ValidationError as exc:
            _record_trace_event(
                self.trace,
                {
                    "stage": "edit.validation.spec_renderer",
                    "status": "rejected",
                    "candidate_files": [item.path for item in rendered.files],
                    "missing_required": (static_validation.get("missing_required") or [])[:20],
                    "missing_artifacts": (artifact_validation.get("missing_artifacts") or [])[:30],
                    "regressions": (artifact_validation.get("regressions") or [])[:20],
                    "error": str(exc),
                },
            )
            return None
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def rendered_edit_fallback(
        self,
        *,
        prompt: str,
        root: Path,
        schema_delta: dict[str, Any],
        edit_contract: dict[str, Any],
        previous_contracts: list[dict[str, Any]] | None,
        saved_memory: dict[str, Any] | None,
        usage: ModelRunUsage,
        reason: str,
    ) -> ModelEditResult | None:
        """Re-render the project deterministically when the model edit failed.

        The model path is tried first so hand-written code survives whenever it
        can. Once that path is exhausted the deterministic renderer is a better
        answer than writing nothing at all, and it is validated exactly like any
        other candidate before it reaches the live project.
        """
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.rendered_fallback",
                "status": "attempted",
                "reason": str(reason)[:600],
            },
        )
        try:
            result = self.try_rendered_edit(
                prompt=prompt,
                root=root,
                schema_delta=schema_delta,
                edit_contract=edit_contract,
                previous_contracts=previous_contracts,
                saved_memory=saved_memory,
                usage=usage,
            )
        except Exception as exc:
            _record_trace_event(
                self.trace,
                {
                    "stage": "edit.rendered_fallback",
                    "status": "error",
                    "error": str(exc)[:600],
                },
            )
            return None
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.rendered_fallback",
                "status": "accepted" if result is not None else "rejected",
                "changed_files": [item.path for item in result.changed] if result is not None else [],
            },
        )
        if result is not None:
            result.usage.retries += 1
        return result

    def plan_schema_delta(
        self,
        *,
        prompt: str,
        index: dict[str, Any],
        saved_memory: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "You are planning only the database/schema impact of a FastAPI edit. Do not write code. "
            "Use saved schema memory and the project index. Return ONLY JSON with keys: summary, no_schema_change, "
            "ddl_sql, schema, affected_tables, affected_fields, assumptions. "
            "If the edit does not require database/schema changes, set no_schema_change=true and explain in summary."
        )
        data, usage = _complete_json_with_repair(
            self.provider,
            system,
            {
                "request": prompt,
                "saved_pipeline_memory": saved_memory or {},
                "project_schema_index": (index.get("database_schema") or {}),
            },
            required_keys=["summary", "no_schema_change", "ddl_sql", "schema", "affected_tables", "affected_fields", "assumptions"],
            max_tokens=3000,
            repair_max_tokens=3600,
            trace=self.trace,
            stage="edit.schema_delta",
        )
        validation = _validate_schema_delta(data, prompt=prompt, index=index)
        data["validation"] = validation
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.schema_delta_validation",
                "status": "accepted" if validation["passed"] else "rejected",
                "tables": validation.get("tables") or [],
                "errors": validation.get("errors") or [],
                "warnings": validation.get("warnings") or [],
            },
        )
        if not validation["passed"]:
            deterministic = _repair_no_change_contradiction_if_targeted(
                data,
                prompt=prompt,
                validation=validation,
            )
            if deterministic is not None:
                repaired_validation = _validate_schema_delta(deterministic, prompt=prompt, index=index)
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.schema_delta_deterministic_repair",
                        "status": "accepted" if repaired_validation["passed"] else "rejected",
                        "tables": repaired_validation.get("tables") or [],
                        "errors": repaired_validation.get("errors") or [],
                        "warnings": repaired_validation.get("warnings") or [],
                    },
                )
                if repaired_validation["passed"]:
                    deterministic["validation"] = repaired_validation
                    return deterministic, usage
                data = deterministic
                validation = repaired_validation
            repair_system = (
                "Repair the schema delta JSON. Return ONLY JSON with keys summary, no_schema_change, ddl_sql, "
                "schema, affected_tables, affected_fields, assumptions. Keep the user request unchanged."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    "request": prompt,
                    "saved_pipeline_memory": saved_memory or {},
                    "project_schema_index": index.get("database_schema") or {},
                    "previous_response": _compact_previous_response(data),
                    "validation_errors": validation.get("errors") or [],
                },
                max_tokens=3600,
                trace=self.trace,
                stage="edit.schema_delta_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            validation = _validate_schema_delta(repaired, prompt=prompt, index=index)
            deterministic = _repair_no_change_contradiction_if_targeted(
                repaired,
                prompt=prompt,
                validation=validation,
            )
            if deterministic is not None:
                repaired = deterministic
                validation = _validate_schema_delta(repaired, prompt=prompt, index=index)
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.schema_delta_deterministic_repair",
                        "status": "accepted" if validation["passed"] else "rejected",
                        "tables": validation.get("tables") or [],
                        "errors": validation.get("errors") or [],
                        "warnings": validation.get("warnings") or [],
                    },
                )
            if not validation["passed"] or not all(
                key in repaired
                for key in ("summary", "no_schema_change", "ddl_sql", "schema", "affected_tables", "affected_fields", "assumptions")
            ):
                strict_system = (
                    "The previous schema delta repair returned valid JSON but not a usable schema delta. "
                    "Return ONLY one JSON object with exactly these keys: summary, no_schema_change, ddl_sql, "
                    "schema, affected_tables, affected_fields, assumptions. Do not echo previous_response, keys, "
                    "validation, validation_errors, request, saved memory, or project index. "
                    "If the user asks for new resources/tables/fields, no_schema_change must be false and schema.tables "
                    "must describe those requested tables."
                )
                strict_repaired, strict_usage = _complete_json(
                    self.provider,
                    strict_system,
                    {
                        "request": prompt,
                        "saved_pipeline_memory": saved_memory or {},
                        "project_schema_index": index.get("database_schema") or {},
                        "invalid_repair_summary": _compact_previous_response(repaired),
                        "repair_validation": validation,
                    },
                    max_tokens=3600,
                    temperature=0.0,
                    trace=self.trace,
                    stage="edit.schema_delta_repair_strict",
                )
                usage.add_usage(strict_usage)
                usage.retries += 1
                repaired = strict_repaired
                validation = _validate_schema_delta(repaired, prompt=prompt, index=index)
            if not validation["passed"]:
                refocus_system = (
                    "The schema delta is still invalid or copied from the existing project. "
                    "Return ONLY one JSON object with keys summary, no_schema_change, ddl_sql, schema, "
                    "affected_tables, affected_fields, assumptions. Use the user's edit request as the source of truth. "
                    "Do not copy existing schema tables unless the user explicitly asks to keep or modify them. "
                    "If the user asks for a new resource/entity, create a matching table in ddl_sql and schema.tables. "
                    "schema.tables names must match CREATE TABLE names."
                )
                refocused, refocus_usage = _complete_json(
                    self.provider,
                    refocus_system,
                    {
                        "request": prompt,
                        "existing_schema_tables": [
                            {
                                "name": table.get("name") or table.get("db_table_name"),
                                "db_table_name": table.get("db_table_name"),
                                "fields": [field.get("name") for field in table.get("fields") or [] if isinstance(field, dict)],
                            }
                            for table in ((index.get("database_schema") or {}).get("tables") or [])
                            if isinstance(table, dict)
                        ],
                        "invalid_schema_delta": _compact_previous_response(repaired),
                        "validation_errors": validation.get("errors") or [],
                    },
                    max_tokens=3600,
                    temperature=0.0,
                    trace=self.trace,
                    stage="edit.schema_delta_repair_refocus",
                )
                usage.add_usage(refocus_usage)
                usage.retries += 1
                repaired = refocused
                validation = _validate_schema_delta(repaired, prompt=prompt, index=index)
            if not validation["passed"] and any(
                "copied existing tables" in error for error in (validation.get("errors") or [])
            ):
                user_focus_system = (
                    "Your schema delta still includes existing project tables that the user's new edit request did not mention. "
                    "Return ONLY one JSON object with keys summary, no_schema_change, ddl_sql, schema, "
                    "affected_tables, affected_fields, assumptions. Keep only tables and fields required by the user's current "
                    "edit request. Do not include unrelated existing project tables. schema.tables names must match CREATE TABLE names."
                )
                user_focused, user_focus_usage = _complete_json(
                    self.provider,
                    user_focus_system,
                    {
                        "request": prompt,
                        "invalid_schema_delta": _compact_previous_response(repaired),
                        "validation_errors": validation.get("errors") or [],
                    },
                    max_tokens=3600,
                    temperature=0.0,
                    trace=self.trace,
                    stage="edit.schema_delta_repair_user_focus",
                )
                usage.add_usage(user_focus_usage)
                usage.retries += 1
                repaired = user_focused
                validation = _validate_schema_delta(repaired, prompt=prompt, index=index)
            repaired["validation"] = validation
            data = repaired
        if not data.get("validation", {}).get("passed"):
            raise ValidationError("model did not return a usable schema delta")
        return data, usage

    def plan_api_delta(
        self,
        *,
        prompt: str,
        index: dict[str, Any],
        schema_delta: dict[str, Any],
        previous_contracts: list[dict[str, Any]] | None = None,
        saved_memory: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "You are planning the API/behavior impact of a FastAPI edit. Do not write code. "
            "Use saved API memory, schema delta, and project index. Return ONLY JSON with keys: summary, "
            "requirements, routes, auth, websocket_events, artifact_contract, target_file_hints, assumptions, missing_info. "
            "artifact_contract must describe only the requested delta and regressions that must remain valid."
        )
        payload = {
            "request": prompt,
            "saved_pipeline_memory": saved_memory or {},
            "schema_delta": schema_delta,
            "project_index": _compact_edit_index(index, []),
            "previous_accepted_contracts": previous_contracts or [],
        }
        try:
            data, usage = _complete_json_with_repair(
                self.provider,
                system,
                payload,
                required_keys=["summary", "requirements", "routes", "auth", "websocket_events", "artifact_contract", "target_file_hints", "assumptions", "missing_info"],
                max_tokens=3600,
                repair_max_tokens=4200,
                trace=self.trace,
                stage="edit.api_delta",
            )
            finalized = _finalize_contract_data(data, prompt=prompt, source="edit")
        except ValidationError as first_error:
            if isinstance(first_error, ModelJsonResponseError):
                data = {"raw_content": first_error.raw_content}
                usage = first_error.usage
            repair_system = (
                "Repair the API delta JSON. Return ONLY one JSON object with keys summary, requirements, routes, "
                "auth, websocket_events, artifact_contract, target_file_hints, assumptions, missing_info. "
                "Do not return package dependencies as requirements."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    **payload,
                    "previous_response": _compact_previous_response(data),
                    "error": str(first_error),
                },
                max_tokens=4200,
                trace=self.trace,
                stage="edit.api_delta_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            try:
                finalized = _finalize_contract_data(repaired, prompt=prompt, source="edit")
            except ValidationError as repair_error:
                strict_repair_system = (
                    "The previous API delta repair returned valid JSON but not a usable edit contract. "
                    "Return ONLY one JSON object with exactly these top-level keys: summary, requirements, routes, "
                    "auth, websocket_events, artifact_contract, target_file_hints, assumptions, missing_info. "
                    "Do not echo the request, saved memory, project index, previous_response, or error. "
                    "requirements must describe requested behavior changes. artifact_contract must contain explicit "
                    "route/table/field/filter/relationship/behavior checks for the edit."
                )
                strict_repaired, strict_usage = _complete_json(
                    self.provider,
                    strict_repair_system,
                    {
                        "request": prompt,
                        "schema_delta": schema_delta,
                        "project_index": _compact_edit_index(index, []),
                        "previous_accepted_contracts": previous_contracts or [],
                        "invalid_repair_summary": _compact_previous_response(repaired),
                        "repair_error": str(repair_error),
                    },
                    max_tokens=3600,
                    temperature=0.0,
                    trace=self.trace,
                    stage="edit.api_delta_repair_strict",
                )
                usage.add_usage(strict_usage)
                usage.retries += 1
                finalized = _finalize_contract_data(strict_repaired, prompt=prompt, source="edit")
        finalized["artifact_contract"] = _merge_artifact_lists(
            finalized["artifact_contract"],
            _schema_delta_artifacts(schema_delta),
        )
        api_schema_errors = _validate_api_delta_against_schema(finalized["artifact_contract"], schema_delta)
        if api_schema_errors:
            schema_focus_system = (
                "The API delta does not match the schema delta from the user's edit request. "
                "Return ONLY one JSON object with exactly these top-level keys: summary, requirements, routes, "
                "auth, websocket_events, artifact_contract, target_file_hints, assumptions, missing_info. "
                "Routes and artifact_contract must target the tables introduced or changed by schema_delta. "
                "Do not copy unrelated existing project routes."
            )
            schema_focused, schema_focus_usage = _complete_json(
                self.provider,
                schema_focus_system,
                {
                    "request": prompt,
                    "schema_delta": schema_delta,
                    "invalid_api_delta": _compact_previous_response(finalized),
                    "validation_errors": api_schema_errors,
                },
                max_tokens=3600,
                temperature=0.0,
                trace=self.trace,
                stage="edit.api_delta_repair_schema_focus",
            )
            usage.add_usage(schema_focus_usage)
            usage.retries += 1
            finalized = _finalize_contract_data(schema_focused, prompt=prompt, source="edit")
            finalized["artifact_contract"] = _merge_artifact_lists(
                finalized["artifact_contract"],
                _schema_delta_artifacts(schema_delta),
            )
            api_schema_errors = _validate_api_delta_against_schema(finalized["artifact_contract"], schema_delta)
            if api_schema_errors:
                raise ValidationError("; ".join(api_schema_errors))
        delta = _contract_delta_for_edit(index, finalized["artifact_contract"])
        delta["target_file_hints"] = finalized.get("target_file_hints") or finalized.get("target_files") or []
        delta["schema_delta"] = schema_delta
        delta["validation"] = {"passed": True}
        return delta, usage

    def build_edit_contract(
        self,
        *,
        prompt: str,
        index: dict[str, Any],
        selected: list[str],
        previous_contracts: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], ModelRunUsage]:
        system = (
            "Return ONLY one JSON object describing the requested FastAPI edit. No code and no prose. "
            "Required keys: summary, requirements, artifact_contract, target_files, missing_info. "
            "requirements must describe user-requested feature changes, not package dependencies. "
            "artifact_contract must explicitly list the routes, tables, fields, filters, relationships, "
            "or behaviors that will prove the edit after code is changed. "
            "Do not copy existing project_index entries unless they must be changed; focus on requested deltas."
        )
        payload = {
            "request": prompt,
            "project_index": _compact_edit_index(index, selected),
            "selected_files": selected,
            "previous_accepted_contracts": previous_contracts or [],
        }
        try:
            data, usage = _complete_json(
                self.provider,
                system,
                payload,
                max_tokens=1800,
                trace=self.trace,
                stage="edit.contract",
            )
            finalized = _finalize_contract_data(data, prompt=prompt, source="edit")
            return _contract_delta_for_edit(index, finalized["artifact_contract"]), usage
        except ValidationError as first_error:
            if isinstance(first_error, ModelJsonResponseError):
                data = {"raw_content": first_error.raw_content}
                usage = first_error.usage
            repair_system = (
                "Repair the FastAPI edit contract JSON. Return ONLY one JSON object with keys "
                "summary, requirements, artifact_contract, target_files, missing_info. "
                "Do not return package dependencies as requirements. artifact_contract must contain explicit checks."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    **payload,
                    "previous_response": _compact_previous_response(data),
                    "error": str(first_error),
                },
                max_tokens=2200,
                trace=self.trace,
                stage="edit.contract_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            finalized = _finalize_contract_data(repaired, prompt=prompt, source="edit")
            return _contract_delta_for_edit(index, finalized["artifact_contract"]), usage

    def build_patch_set(
        self,
        *,
        prompt: str,
        root: Path,
        index: dict[str, Any],
        previous_contracts: list[dict[str, Any]] | None = None,
        edit_contract: dict[str, Any] | None = None,
        selected: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str], ModelRunUsage]:
        selected = selected or select_edit_files(root, index, prompt, requirement_contracts=previous_contracts)
        if not selected:
            raise ValidationError("No editable files were selected for this request")
        selected = _codegen_files(selected)
        files = [
            (
                _compact_file_for_patch(
                    root,
                    path,
                    contract=edit_contract,
                    max_chars=MAX_PATCH_FILE_CONTEXT_CHARS,
                )
                if edit_contract
                else _compact_file(root, path, max_chars=MAX_PATCH_FILE_CONTEXT_CHARS)
            )
            for path in selected
            if (root / path).exists()
        ]
        if edit_contract:
            system = (
                "Return ONLY one JSON object for a FastAPI patch edit. No prose. "
                "Use edit_contract as the requirements/artifact source of truth. "
                "Required top-level keys: summary, target_files, patches, missing_info. "
                "Do not repeat the full edit_contract. patches must be non-empty. "
                "Allowed patch ops: replace_text(op,path,old,new), insert_before(op,path,anchor,content), "
                "insert_after(op,path,anchor,content), delete_text(op,path,old), create_file(op,path,content), "
                "insert_import(op,path,statement), insert_class_field(op,path,class_name,content), "
                "insert_router_wiring(op,path,import_statement,include_statement), append_function(op,path,content). "
                "Patch anchors must be exact unique text from source_files."
            )
        else:
            system = (
                "Return ONLY one JSON object for a FastAPI patch edit. No prose. "
                "Use only source_files and project_index. Do not invent a fixed template. "
                "Required top-level keys: summary, requirements, artifact_contract, target_files, patches, missing_info. "
                "requirements must be non-empty. artifact_contract must explicitly list the routes/tables/fields/relationships "
                "that prove the edit. patches must be non-empty. "
                "Allowed patch ops: replace_text(op,path,old,new), insert_before(op,path,anchor,content), "
                "insert_after(op,path,anchor,content), delete_text(op,path,old), create_file(op,path,content), "
                "insert_import(op,path,statement), insert_class_field(op,path,class_name,content), "
                "insert_router_wiring(op,path,import_statement,include_statement), append_function(op,path,content). "
                "Patch anchors must be exact unique text from source_files."
            )
        payload = {
            "request": prompt,
            "project_index": _compact_code_context(index, selected),
            "previous_accepted_contracts": previous_contracts or [],
            "selected_files": selected,
            "source_files": files,
        }
        if edit_contract:
            payload["edit_contract"] = _compact_contract_for_prompt(edit_contract)
        try:
            data, usage = _complete_json(
                self.provider,
                system,
                payload,
                max_tokens=4500,
                trace=self.trace,
                stage="edit.patch",
            )
            return _finalize_patch_set(data, prompt=prompt, fallback_contract=edit_contract), selected, usage
        except ValidationError as first_error:
            if isinstance(first_error, ModelJsonResponseError):
                data = {"raw_content": first_error.raw_content}
                usage = first_error.usage
            repair_system = (
                "Repair the model patch JSON. Return ONLY JSON with keys summary, requirements, "
                "artifact_contract, target_files, patches, missing_info. Do not summarize. Do not copy schema examples. "
                "Return real patch operations against source_files using exact unique anchors."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    "request": prompt,
                    "project_index": _compact_code_context(index, selected),
                    "edit_contract": _compact_contract_for_prompt(edit_contract) if edit_contract else {},
                    "previous_response": _compact_previous_response(data),
                    "error": str(first_error),
                    "selected_files": selected,
                    "source_files": files,
                },
                max_tokens=5000,
                trace=self.trace,
                stage="edit.patch_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            return _finalize_patch_set(repaired, prompt=prompt, fallback_contract=edit_contract), selected, usage

    def build_full_file_edit(
        self,
        *,
        prompt: str,
        root: Path,
        index: dict[str, Any],
        selected: list[str],
        previous_contracts: list[dict[str, Any]] | None = None,
        edit_contract: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[FileSpec], ModelRunUsage]:
        selected = _codegen_files(selected)
        files = [
            _compact_file(root, path, max_chars=MAX_FULL_FILE_EDIT_CONTEXT_CHARS)
            for path in selected
            if (root / path).exists()
        ]
        system = (
            "Return ONLY one JSON object for a FastAPI full-file edit. No prose. "
            "Use the current source_files and project_index; do not invent a fixed template. "
            "Required top-level keys: summary, requirements, artifact_contract, files, missing_info. "
            "requirements must be non-empty. artifact_contract must explicitly list the routes/tables/fields/relationships "
            "that prove the edit. files must contain complete updated content for each changed selected file."
        )
        try:
            data, usage = _complete_json(
                self.provider,
                system,
                {
                    "request": prompt,
                    "project_index": _compact_code_context(index, selected),
                    "edit_contract": _compact_contract_for_prompt(edit_contract) if edit_contract else {},
                    "previous_accepted_contracts": previous_contracts or [],
                    "selected_files": selected,
                    "source_files": files,
                },
                max_tokens=8000,
                trace=self.trace,
                stage="edit.full_file",
            )
            data, file_specs = _normalize_full_file_edit(data, prompt=prompt, fallback_contract=edit_contract)
            return data, file_specs, usage
        except ValidationError as first_error:
            if isinstance(first_error, ModelJsonResponseError):
                data = {"raw_content": first_error.raw_content}
                usage = first_error.usage
            if not edit_contract:
                try:
                    edit_contract, contract_usage = self.build_edit_contract(
                        prompt=prompt,
                        index=index,
                        selected=selected,
                        previous_contracts=previous_contracts,
                    )
                    usage.add_usage(contract_usage)
                    usage.retries += 1
                    data_with_contract, file_specs = _normalize_full_file_edit(
                        data,
                        prompt=prompt,
                        fallback_contract=edit_contract,
                    )
                    return data_with_contract, file_specs, usage
                except ValidationError:
                    pass
            repair_system = (
                "Repair the FastAPI full-file edit JSON. Return ONLY one JSON object with keys "
                "summary, requirements, artifact_contract, files, missing_info. No prose. "
                "Do not say success unless files contains complete updated source code. "
                "requirements and artifact_contract must be non-empty and match the user's requested edit."
            )
            repaired, repair_usage = _complete_json(
                self.provider,
                repair_system,
                {
                    "request": prompt,
                    "project_index": _compact_code_context(index, selected),
                    "previous_response": _compact_previous_response(data),
                    "error": str(first_error),
                    "edit_contract": _compact_contract_for_prompt(edit_contract) if edit_contract else {},
                    "selected_files": selected,
                    "source_files": files,
                },
                max_tokens=8500,
                trace=self.trace,
                stage="edit.full_file_repair",
            )
            usage.add_usage(repair_usage)
            usage.retries += 1
            data, file_specs = _normalize_full_file_edit(
                repaired,
                prompt=prompt,
                fallback_contract=edit_contract,
            )
            return data, file_specs, usage

    def repair_validated_edit(
        self,
        *,
        prompt: str,
        root: Path,
        index: dict[str, Any],
        selected: list[str],
        previous_contracts: list[dict[str, Any]] | None,
        edit_contract: dict[str, Any],
        failed_patch_set: dict[str, Any],
        validation: dict[str, Any],
        repair_task: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[FileSpec], ModelRunUsage]:
        selected = _codegen_files(selected)
        files = [
            _compact_file(root, path, max_chars=MAX_FULL_FILE_EDIT_CONTEXT_CHARS)
            for path in selected
            if (root / path).exists()
        ]
        task_text = str((repair_task or {}).get("objective") or "Fix the next validation failure group.")
        system = (
            "The previous FastAPI edit applied to a temporary project but failed one validation group. "
            "Return ONLY one JSON object for a small focused full-file edit. No prose. "
            "Required top-level keys: summary, requirements, artifact_contract, files, missing_info. "
            "files must contain complete updated source only for files needed by repair_task. "
            f"Focus only on this repair task: {task_text} "
            "Do not broaden the edit, do not rewrite unrelated files, and do not try to solve unrelated validation groups. "
            "Do not return a patch; return full files for the focused files."
        )
        focused_candidate = {
            "summary": failed_patch_set.get("summary") if isinstance(failed_patch_set, dict) else "",
            "patches": [
                op for op in (failed_patch_set.get("patches") or [])
                if isinstance(op, dict) and safe_rel(str(op.get("path") or "")) in {safe_rel(item) for item in selected}
            ],
            "target_files": selected,
        }
        focused_validation = {
            "errors": (validation.get("errors") or [])[:8],
            "missing_required": (validation.get("missing_required") or [])[:8],
            "missing_artifacts": (validation.get("missing_artifacts") or [])[:8],
            "regressions": (validation.get("regressions") or [])[:8],
        }
        data, usage = _complete_json(
            self.provider,
            system,
            {
                "request": prompt,
                "project_index": _compact_code_context(index, selected),
                "edit_contract": _compact_contract_for_prompt(edit_contract),
                "previous_accepted_contracts": previous_contracts or [],
                "selected_files": selected,
                "source_files": files,
                "failed_candidate": focused_candidate,
                "validation": focused_validation,
                "repair_task": repair_task or {},
            },
            max_tokens=5200,
            temperature=0.0,
            trace=self.trace,
            stage="edit.validation_repair_step",
        )
        data, file_specs = _normalize_full_file_edit(data, prompt=prompt, fallback_contract=edit_contract)
        usage.retries += 1
        return data, file_specs, usage

    def edit(
        self,
        *,
        prompt: str,
        root: Path,
        index: dict[str, Any],
        previous_contracts: list[dict[str, Any]] | None = None,
        validation_contracts: list[dict[str, Any]] | None = None,
        saved_memory: dict[str, Any] | None = None,
        selected_files: list[str] | None = None,
        model_first: bool = False,
    ) -> ModelEditResult:
        self.trace = []
        preservation_contracts = (
            validation_contracts
            if validation_contracts is not None
            else previous_contracts
        )
        saved_schema = (saved_memory or {}).get("schema")
        existing_intent_schema = (
            saved_schema
            if isinstance(saved_schema, dict)
            else index.get("database_schema")
            if isinstance(index.get("database_schema"), dict)
            else None
        )
        prompt_intent = extract_prompt_intent(
            prompt,
            existing_schema=existing_intent_schema,
        )
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.prompt_intent",
                "status": "ok",
                "contract": prompt_intent.get("contract") or {},
                "schema_additions": prompt_intent.get("schema_additions") or {},
                "custom_routes": prompt_intent.get("custom_routes") or [],
                "auth_policy": prompt_intent.get("auth_policy") or {},
            },
        )
        schema_usage = ModelRunUsage()
        intent_contract = prompt_intent.get("contract") if isinstance(prompt_intent.get("contract"), dict) else {}
        deterministic_first = bool(
            prompt_intent.get("custom_routes")
            or (prompt_intent.get("schema_additions") or {}).get("tables")
            or any(intent_contract.get(key) for key in (
                "required_routes",
                "required_tables",
                "required_fields",
                "required_filters",
                "required_relationships",
                "required_behaviors",
            ))
        )
        schema_delta = _deterministic_schema_delta_from_intent(prompt, prompt_intent, index=index) if deterministic_first else None
        if schema_delta is not None:
            _record_trace_event(
                self.trace,
                {
                    "stage": "edit.schema_delta",
                    "status": "deterministic",
                    "tables": (schema_delta.get("validation") or {}).get("tables") or [],
                    "reason": "prompt_intent_custom_route_fast_path",
                },
            )
        else:
            try:
                schema_delta, schema_usage = self.plan_schema_delta(
                    prompt=prompt,
                    index=index,
                    saved_memory=saved_memory,
                )
                schema_delta = _apply_prompt_intent_to_schema_delta(schema_delta, prompt_intent)
            except ValidationError as exc:
                schema_delta = _deterministic_schema_delta_from_intent(prompt, prompt_intent, index=index)
                if schema_delta is None:
                    raise
                schema_usage = ModelRunUsage()
                schema_usage.retries += 1
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.schema_delta_prompt_fallback",
                        "status": "ok",
                        "tables": (schema_delta.get("validation") or {}).get("tables") or [],
                        "reason": "prompt_intent_after_model_failure",
                        "error": str(exc),
                    },
                )

        contract_usage = ModelRunUsage()
        edit_contract = _deterministic_api_delta_from_intent(prompt, prompt_intent) if deterministic_first else None
        if edit_contract is not None:
            _record_trace_event(
                self.trace,
                {
                    "stage": "edit.api_delta",
                    "status": "deterministic",
                    "artifact_contract": edit_contract.get("artifact_contract") or {},
                    "reason": "prompt_intent_custom_route_fast_path",
                },
            )
        else:
            try:
                edit_contract, contract_usage = self.plan_api_delta(
                    prompt=prompt,
                    index=index,
                    schema_delta=schema_delta,
                    previous_contracts=previous_contracts,
                    saved_memory=saved_memory,
                )
            except Exception as exc:
                edit_contract = _deterministic_api_delta_from_intent(prompt, prompt_intent)
                if edit_contract is None:
                    edit_contract = _fallback_api_delta_from_intent(prompt, prompt_intent)
                contract_usage = ModelRunUsage()
                contract_usage.retries += 1
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.api_delta_prompt_fallback",
                        "status": "ok",
                        "artifact_contract": edit_contract.get("artifact_contract") or {},
                        "error": str(exc),
                    },
                )
            edit_contract = merge_intent_into_api_contract(edit_contract, prompt_intent)
        canonical_spec = build_canonical_edit_spec(
            prompt=prompt,
            index=index,
            schema_delta=schema_delta,
            api_delta=edit_contract,
            prompt_intent=prompt_intent,
        )
        schema_delta = enrich_schema_plan_from_canonical_spec(schema_delta, canonical_spec)
        edit_contract["artifact_contract"] = merge_canonical_spec_into_contract(
            edit_contract.get("artifact_contract") or edit_contract,
            canonical_spec,
        )
        edit_contract["canonical_spec"] = canonical_spec
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.canonical_spec",
                "status": "ok",
                "summary": canonical_spec.get("summary") or {},
            },
        )
        selection_prompt = json.dumps(
            {
                "request": prompt,
                "canonical_spec": canonical_spec,
                "schema_delta": schema_delta,
                "api_delta": {
                    "requirements": edit_contract.get("requirements") or [],
                    "artifact_contract": edit_contract.get("artifact_contract") or {},
                    "target_file_hints": edit_contract.get("target_file_hints") or [],
                },
            },
            ensure_ascii=False,
            default=str,
        )
        if selected_files is not None:
            selected = [path for path in dict.fromkeys(selected_files) if (root / path).exists()]
        else:
            selected = select_edit_files(
                root,
                index,
                selection_prompt,
                requirement_contracts=[*(previous_contracts or []), edit_contract],
            )
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.selection",
                "status": "ok" if selected else "rejected",
                "selected_files": selected,
                "previous_contracts": len(previous_contracts or []),
            },
        )
        selected_context_chars = sum(
            len(_compact_file(root, path, max_chars=MAX_FULL_FILE_EDIT_CONTEXT_CHARS))
            for path in selected
            if (root / path).exists()
        )
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.context_budget",
                "selected_files": len(selected),
                "selected_context_chars": selected_context_chars,
                "estimated_context_tokens": round(selected_context_chars / 4),
                "selection_scope": "targeted_files",
            },
        )
        if not selected:
            raise ValidationError("No editable files were selected for this request")
        contract_usage.add_usage(schema_usage)
        # When the prompt alone determined the whole delta and the project came
        # from the renderer, the deterministic path can express the edit without
        # a single code-generation call. Trying it first turns minutes of model
        # calls into a local render; anything less literal stays model-first.
        renderer_first = not model_first or (deterministic_first and _renderer_owned_project(root))
        _record_trace_event(
            self.trace,
            {
                "stage": "edit.strategy",
                "status": "renderer_first" if renderer_first else "model_first",
                "deterministic_intent": deterministic_first,
                "renderer_owned": _renderer_owned_project(root),
            },
        )
        rendered_edit = None
        if renderer_first:
            rendered_edit = self.try_rendered_edit(
                prompt=prompt,
                root=root,
                schema_delta=schema_delta,
                edit_contract=edit_contract,
                previous_contracts=preservation_contracts,
                saved_memory=saved_memory,
                usage=contract_usage,
            )
        if rendered_edit is not None:
            return rendered_edit
        try:
            patch_set, selected, usage = self.build_patch_set(
                prompt=prompt,
                root=root,
                index=index,
                previous_contracts=previous_contracts,
                edit_contract=edit_contract,
                selected=selected,
            )
            usage.add_usage(contract_usage)
            patch_files: list[FileSpec] | None = None
        except ValidationError as patch_error:
            try:
                patch_set, patch_files, usage = self.build_full_file_edit(
                    prompt=prompt,
                    root=root,
                    index=index,
                    selected=selected,
                    previous_contracts=previous_contracts,
                    edit_contract=edit_contract,
                )
                usage.add_usage(contract_usage)
            except ValidationError as full_file_error:
                fallback = self.rendered_edit_fallback(
                    prompt=prompt,
                    root=root,
                    schema_delta=schema_delta,
                    edit_contract=edit_contract,
                    previous_contracts=preservation_contracts,
                    saved_memory=saved_memory,
                    usage=contract_usage,
                    reason=f"patch: {patch_error}; full_file: {full_file_error}",
                )
                if fallback is not None:
                    return fallback
                raise ModelPatchValidationError(
                    str(full_file_error),
                    patch_set={
                        "summary": "The model did not return an applicable patch or full-file edit.",
                        "requirements": edit_contract.get("requirements") or [],
                        "artifact_contract": edit_contract,
                        "patch_error": str(patch_error),
                        "full_file_error": str(full_file_error),
                        "canonical_spec": canonical_spec,
                    },
                    selected_files=selected,
                    usage=contract_usage,
                    validation={},
                    stage_outputs={"canonical_spec": canonical_spec, "trace": self.trace},
                ) from full_file_error
        validation_repair_attempts = 0
        validation_repair_tasks: list[dict[str, Any]] = []
        while True:
            temp_root = _copy_project_to_temp(root)
            static_validation: dict[str, Any] = {}
            artifact_validation: dict[str, Any] = {}
            temp_index: dict[str, Any] = {}
            changed: list[WriteOutcome] = []
            validated_changed: list[WriteOutcome] = []
            edit_units: list[dict[str, Any]] = []
            try:
                candidate_units = _candidate_edit_units(patch_set, patch_files)
                if not candidate_units:
                    raise ValidationError("The edit candidate did not contain any file units.")
                for sequence, unit in enumerate(candidate_units, start=1):
                    unit_changed = _apply_candidate_unit(temp_root, unit)
                    unit_static = validate_written_project(temp_root, unit_changed).as_dict()
                    unit_result = {
                        "sequence": sequence,
                        "path": unit["path"],
                        "status": "validated" if unit_static.get("passed") else "rejected",
                        "changed_files": [item.path for item in unit_changed],
                        "static_validation": unit_static,
                    }
                    edit_units.append(unit_result)
                    _record_trace_event(
                        self.trace,
                        {
                            "stage": "edit.unit_validation",
                            **unit_result,
                        },
                    )
                    if not unit_static.get("passed"):
                        static_validation = unit_static
                        raise ValidationError(
                            f"Edit unit {sequence} for {unit['path']} failed static validation: "
                            + json.dumps(unit_static, ensure_ascii=False, default=str)[:1000]
                        )
                    # A unit is checkpoint-safe once its own source validation passes.
                    validated_changed.extend(unit_changed)
                    changed.extend(unit_changed)
                static_validation = validate_written_project(temp_root, changed).as_dict()
                temp_index = build_project_index(temp_root)
                artifact_validation = validate_artifacts(
                    temp_index,
                    patch_set["artifact_contract"],
                    previous_contracts=preservation_contracts or [],
                ).as_dict()
                runtime_validation = validate_runtime_project(temp_root)
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.validation",
                        "status": (
                            "accepted"
                            if static_validation.get("passed") and artifact_validation.get("passed") and runtime_validation.get("passed")
                            else "static_safe"
                            if static_validation.get("passed") and not artifact_validation.get("regressions")
                            else "rejected"
                        ),
                        "candidate_files": [item.path for item in changed],
                        "missing_required": (static_validation.get("missing_required") or [])[:20],
                        "missing_artifacts": (artifact_validation.get("missing_artifacts") or [])[:30],
                        "regressions": (artifact_validation.get("regressions") or [])[:20],
                        "runtime_errors": (runtime_validation.get("errors") or [])[:10],
                    },
                )
                if (
                    not static_validation.get("passed")
                    or not artifact_validation.get("passed")
                    or not runtime_validation.get("passed")
                ):
                    raise ValidationError(
                        "Patch output failed validation: "
                        + json.dumps(
                            {
                                "static": static_validation,
                                "artifact_validation": artifact_validation,
                                "runtime_validation": runtime_validation,
                            },
                            ensure_ascii=False,
                            default=str,
                        )[:1500]
                    )
                active_changed: list[WriteOutcome] = []
                for outcome in changed:
                    source = temp_root / outcome.path
                    content = source.read_text(encoding="utf-8")
                    active = write_text_if_changed(root, outcome.path, content)
                    if active:
                        active_changed.append(active)
                capability_plan = build_capability_plan(canonical_spec, patch_set.get("artifact_contract") or edit_contract.get("artifact_contract") or {})
                pipeline_audit = build_pipeline_audit(
                    accepted=True,
                    capability_plan=capability_plan,
                    quest_results=None,
                    validation={
                        "static": static_validation,
                        "artifact_validation": artifact_validation,
                        "runtime_validation": runtime_validation,
                        "accepted": True,
                        "static_safe": bool(static_validation.get("passed")),
                    },
                    usage=usage,
                    changed_files=[item.path for item in active_changed],
                )
                return ModelEditResult(
                    changed=active_changed,
                    requirements=patch_set["requirements"],
                    artifact_contract=patch_set["artifact_contract"],
                    edit_plan={
                        "intent": "model_full_file_edit" if patch_files is not None else "model_patch_edit",
                        "summary": patch_set.get("summary") or "",
                        "target_files": patch_set.get("target_files") or selected,
                        "steps": patch_set.get("patches") or [],
                        "files": [item.path for item in patch_files] if patch_files is not None else [],
                        "requirements": patch_set["requirements"],
                        "artifact_contract": patch_set["artifact_contract"],
                        "canonical_spec": canonical_spec,
                        "schema_delta": schema_delta,
                        "api_delta": edit_contract,
                        "validation_repaired": validation_repair_attempts > 0,
                        "validation_repair_attempts": validation_repair_attempts,
                        "validation_repair_tasks": validation_repair_tasks,
                        "edit_units": edit_units,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                    },
                    provider=provider_label(self.provider, suffix="edit"),
                    usage=usage,
                    selected_files=selected,
                    stage_outputs={
                        "schema_delta": schema_delta,
                        "canonical_spec": canonical_spec,
                        "api_delta": edit_contract,
                        "capability_plan": capability_plan,
                        "pipeline_audit": pipeline_audit,
                        "trace": self.trace,
                    },
                )
            except ValidationError as exc:
                details: dict[str, Any] = {}
                if static_validation:
                    details["static"] = static_validation
                if artifact_validation:
                    details["artifact_validation"] = artifact_validation
                _record_trace_event(
                    self.trace,
                    {
                        "stage": "edit.validation",
                        "status": "rejected",
                        "candidate_files": [item.path for item in patch_files] if patch_files is not None else patch_set.get("target_files"),
                        "missing_required": (static_validation.get("missing_required") or [])[:20],
                        "missing_artifacts": (artifact_validation.get("missing_artifacts") or [])[:30],
                        "regressions": (artifact_validation.get("regressions") or [])[:20],
                        "error": str(exc),
                    },
                )
                # Preserve statically safe units even when a later unit fails.
                if validated_changed and str(exc).startswith("Edit unit"):
                    active_partial: list[WriteOutcome] = []
                    for outcome in validated_changed:
                        source = temp_root / outcome.path
                        if not source.is_file():
                            continue
                        active = write_text_if_changed(root, outcome.path, source.read_text(encoding="utf-8"))
                        if active:
                            active_partial.append(active)
                    if active_partial:
                        partial_plan = {
                            **patch_set,
                            "summary": patch_set.get("summary") or "Applied valid edit units; a later unit needs repair.",
                            "partial": True,
                            "partial_failure": str(exc),
                            "edit_units": edit_units,
                            "validation_repair_attempts": validation_repair_attempts,
                        }
                        _record_trace_event(self.trace, {
                            "stage": "edit.partial_checkpoint",
                            "status": "written_with_warnings",
                            "changed_files": [item.path for item in active_partial],
                            "failed_unit": str(exc)[:1000],
                        })
                        return ModelEditResult(
                            changed=active_partial,
                            requirements=patch_set.get("requirements") or [],
                            artifact_contract=patch_set.get("artifact_contract") or {},
                            edit_plan=partial_plan,
                            provider=provider_label(self.provider, suffix="edit"),
                            usage=usage,
                            selected_files=selected,
                            stage_outputs={"canonical_spec": canonical_spec, "trace": self.trace, "partial_failure": str(exc), "edit_units": edit_units},
                        )
                repairable = _validation_error_is_repairable(
                    exc,
                    static_validation=static_validation,
                    artifact_validation=artifact_validation,
                )
                if not validation_repair_tasks:
                    validation_repair_tasks = _validation_repair_tasks(
                        selected=selected,
                        static_validation=static_validation,
                        artifact_validation=artifact_validation,
                    )
                if validation_repair_attempts >= len(validation_repair_tasks) or validation_repair_attempts >= MAX_VALIDATION_REPAIR_TASKS or not repairable:
                    fallback = self.rendered_edit_fallback(
                        prompt=prompt,
                        root=root,
                        schema_delta=schema_delta,
                        edit_contract=edit_contract,
                        previous_contracts=preservation_contracts,
                        saved_memory=saved_memory,
                        usage=usage,
                        reason=str(exc),
                    )
                    if fallback is not None:
                        return fallback
                    raise ModelPatchValidationError(
                        str(exc),
                        patch_set=patch_set,
                        selected_files=selected,
                        usage=usage,
                        validation=details,
                        stage_outputs={"canonical_spec": canonical_spec, "trace": self.trace},
                    ) from exc
                repair_task = validation_repair_tasks[validation_repair_attempts]
                task_files = [
                    path
                    for path in (repair_task.get("files") or selected)
                    if _is_safe_file_path(str(path)) and ((temp_root / safe_rel(str(path))).exists() or str(path) in selected)
                ] or selected
                if patch_files is None and changed:
                    patch_files = _file_specs_from_outcomes(temp_root, changed)
                try:
                    repaired_patch_set, repaired_files, repair_usage = self.repair_validated_edit(
                        prompt=prompt,
                        root=temp_root,
                        index=temp_index or index,
                        selected=task_files,
                        previous_contracts=previous_contracts,
                        edit_contract=edit_contract,
                        failed_patch_set=patch_set,
                        validation=details,
                        repair_task=repair_task,
                    )
                except Exception as repair_error:
                    details["validation_repair_error"] = str(repair_error)
                    fallback = self.rendered_edit_fallback(
                        prompt=prompt,
                        root=root,
                        schema_delta=schema_delta,
                        edit_contract=edit_contract,
                        previous_contracts=preservation_contracts,
                        saved_memory=saved_memory,
                        usage=usage,
                        reason=f"{exc}; repair: {repair_error}",
                    )
                    if fallback is not None:
                        return fallback
                    raise ModelPatchValidationError(
                        str(exc),
                        patch_set=patch_set,
                        selected_files=selected,
                        usage=usage,
                        validation=details,
                        stage_outputs={"canonical_spec": canonical_spec, "trace": self.trace},
                    ) from exc
                usage.add_usage(repair_usage)
                patch_set = {
                    **repaired_patch_set,
                    "requirements": repaired_patch_set.get("requirements") or patch_set["requirements"],
                    "artifact_contract": repaired_patch_set.get("artifact_contract") or patch_set["artifact_contract"],
                }
                patch_files = _merge_repaired_file_specs(patch_files or [], repaired_files)
                validation_repair_attempts += 1
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
