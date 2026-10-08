"""Compact project context for LLM calls.

The LLM should see a searchable map of the project before it reads or rewrites
full files. This module turns the persisted project index into bounded JSON so
token use stays predictable without hiding important code structure.
"""
from __future__ import annotations

import json
import re
from typing import Any

CHAT_CONTEXT_MAX_CHARS = 6000
PLANNER_CONTEXT_MAX_CHARS = 9000
HISTORY_MESSAGE_MAX_CHARS = 900
TOKEN_CHARS = 4
STOPWORDS = {
    "add",
    "and",
    "api",
    "app",
    "build",
    "change",
    "create",
    "delete",
    "edit",
    "for",
    "from",
    "get",
    "make",
    "model",
    "new",
    "project",
    "route",
    "show",
    "the",
    "this",
    "update",
    "with",
}


def _trim_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _keywords(query: str | None) -> set[str]:
    if not query:
        return set()
    words = {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", query)
        if word.lower() not in STOPWORDS
    }
    expanded = set(words)
    for word in words:
        if word.endswith("s") and len(word) > 3:
            expanded.add(word[:-1])
        else:
            expanded.add(f"{word}s")
    return expanded


def _search_blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_search_blob(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_search_blob(v) for v in value)
    return str(value or "")


def _ranked(items: list[dict[str, Any]], query: str | None, *, limit: int) -> list[dict[str, Any]]:
    terms = _keywords(query)
    if not terms:
        return items[:limit]

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for pos, item in enumerate(items):
        blob = _search_blob(item).lower()
        score = sum(1 for term in terms if term in blob)
        if score:
            scored.append((score, -pos, item))

    selected = [item for _, _, item in sorted(scored, reverse=True)]
    if len(selected) < limit:
        seen = {id(item) for item in selected}
        selected.extend(item for item in items if id(item) not in seen)
    return selected[:limit]


def _pick_table(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": table.get("name"),
        "file": table.get("file"),
        "fields": [
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "fk": field.get("foreign_key"),
                "pk": bool(field.get("primary_key")),
                "indexed": bool(field.get("index")),
            }
            for field in (table.get("fields") or [])[:16]
        ],
    }


def _pick_param(param: dict[str, Any]) -> dict[str, Any]:
    item = {
        "name": param.get("name"),
        "in": param.get("in"),
        "type": param.get("type"),
        "required": bool(param.get("required")),
    }
    if "default" in param:
        item["default"] = param.get("default")
    return item


def _pick_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": route.get("method"),
        "path": route.get("path"),
        "file": route.get("file"),
        "function": route.get("function"),
        "line": route.get("line"),
        "params": [_pick_param(param) for param in (route.get("parameters") or [])[:16]],
        "response_model": route.get("response_model") or None,
        "body_schemas": (route.get("body_schema_names") or [])[:8] or None,
        "status_code": route.get("status_code"),
    }


def _pick_file(file_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": file_info.get("path"),
        "bytes": file_info.get("bytes"),
        "sha": _trim_text(file_info.get("sha256", ""), 12),
        "imports": (file_info.get("imports") or [])[:12] or None,
        "tables": file_info.get("tables") or None,
        "routes": file_info.get("routes") or None,
    }


def _pick_function(fn: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": fn.get("file"),
        "name": fn.get("name"),
        "sig": fn.get("signature"),
        "line": fn.get("line"),
        "route": fn.get("route"),
        "async": bool(fn.get("is_async")),
        "symbols": (fn.get("body_symbols") or [])[:32] or None,
        "query_filters": [
            {
                "table": item.get("table"),
                "field": item.get("field"),
                "op": item.get("operator"),
                "value": item.get("value"),
            }
            for item in (fn.get("query_filters") or [])[:12]
        ] or None,
        "summary": _trim_text(fn.get("summary", ""), 180),
    }


def _pick_class(class_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": class_info.get("file"),
        "name": class_info.get("name"),
        "line": class_info.get("line"),
        "bases": (class_info.get("bases") or [])[:8],
        "db_table": class_info.get("db_table_name") or None,
        "kind": (
            "sqlmodel_table"
            if class_info.get("is_sqlmodel_table")
            else "sqlalchemy_table"
            if class_info.get("is_sqlalchemy_table")
            else "pydantic_model"
            if class_info.get("is_pydantic_model")
            else "class"
        ),
        "fields": [
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "line": field.get("line"),
                "fk": field.get("foreign_key"),
                "pk": bool(field.get("primary_key")),
                "indexed": bool(field.get("index")),
                "nullable": field.get("nullable"),
            }
            for field in (class_info.get("fields") or [])[:24]
        ],
    }


def _pick_contract(contract: dict[str, Any]) -> dict[str, Any]:
    requirements = contract.get("requirements") or []
    coverage = contract.get("coverage") or contract.get("requirement_coverage") or {}
    return {
        "id": contract.get("id"),
        "source": contract.get("source"),
        "prompt": _trim_text(contract.get("prompt", ""), 500),
        "requirements": [
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "description": _trim_text(item.get("description", ""), 220),
                "entities": (item.get("entities") or [])[:8],
            }
            for item in requirements[:20]
            if isinstance(item, dict)
        ],
        "coverage": {
            "passed": coverage.get("passed"),
            "covered": (coverage.get("covered") or [])[:20],
            "missing": (coverage.get("missing") or [])[:20],
            "partial": (coverage.get("partial") or [])[:10],
        } if isinstance(coverage, dict) else {},
        "files": (contract.get("changed_files") or contract.get("files") or [])[:16],
    }


def compact_history(rows: list[Any], *, limit: int = 8) -> list[dict[str, str]]:
    selected = rows[-limit:] if len(rows) > limit else rows
    return [
        {
            "message_type": getattr(row, "message_type", ""),
            "content": _trim_text(getattr(row, "content", ""), HISTORY_MESSAGE_MAX_CHARS),
        }
        for row in selected
    ]


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + TOKEN_CHARS - 1) // TOKEN_CHARS) if text else 0


def _serialize_payload(payload: dict[str, Any], max_chars: int | None = None) -> str:
    compact = json.dumps(
        {k: v for k, v in payload.items() if v},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if compact == "{}":
        return ""
    return _trim_text(compact, max_chars) if max_chars else compact


def _project_context_payload(
    *,
    analysis: Any | None = None,
    helper: Any | None = None,
    query: str | None = None,
    table_limit: int = 24,
    route_limit: int = 60,
    file_limit: int = 80,
    function_limit: int = 80,
) -> dict[str, Any]:
    tables = []
    routes = []
    files = []
    functions = []
    classes = []
    recent_changes = []
    requirement_contracts = []
    if helper:
        schema = getattr(helper, "database_schema", None) or {}
        tables = _ranked(
            schema.get("tables") or [],
            query,
            limit=table_limit,
        )
        routes = _ranked(getattr(helper, "api_routes", None) or [], query, limit=route_limit)
        files = _ranked(getattr(helper, "file_index", None) or [], query, limit=file_limit)
        functions = _ranked(
            getattr(helper, "function_summaries", None) or [],
            query,
            limit=function_limit,
        )
        classes = _ranked(schema.get("class_summaries") or [], query, limit=function_limit)
        recent_changes = (getattr(helper, "recent_changes", None) or [])[:12]
        requirement_contracts = [
            contract
            for contract in (getattr(helper, "requirement_contracts", None) or [])
            if isinstance(contract, dict)
            and contract.get("accepted") is True
        ][:8]

    return {
        "requirements": _trim_text(getattr(analysis, "requirements_summary", ""), 700),
        "analysis_schema": _trim_text(getattr(analysis, "database_schema", ""), 900),
        "project": _trim_text(getattr(helper, "project_context", ""), 700),
        "db_tables": [_pick_table(table) for table in tables],
        "api_routes": [_pick_route(route) for route in routes],
        "files": [_pick_file(file_info) for file_info in files],
        "classes": [_pick_class(class_info) for class_info in classes],
        "functions": [_pick_function(fn) for fn in functions],
        "requirement_contracts": [
            _pick_contract(contract)
            for contract in requirement_contracts
            if isinstance(contract, dict)
        ],
        "recent_changes": recent_changes,
    }


def context_token_savings(
    *,
    analysis: Any | None = None,
    helper: Any | None = None,
    max_chars: int = CHAT_CONTEXT_MAX_CHARS,
    query: str | None = None,
) -> dict[str, int | float]:
    """Estimate tokens saved by selected SQL context versus the whole SQL index.

    This is an approximation using 4 chars/token. It measures prompt context
    assembly, not provider-reported billing tokens.
    """
    if not analysis and not helper:
        return {
            "full_context_estimated_tokens": 0,
            "selected_context_estimated_tokens": 0,
            "context_tokens_saved": 0,
            "context_tokens_saved_pct": 0.0,
        }

    full_payload = _project_context_payload(
        analysis=analysis,
        helper=helper,
        query=None,
        table_limit=10_000,
        route_limit=10_000,
        file_limit=10_000,
        function_limit=10_000,
    )
    selected_payload = _project_context_payload(
        analysis=analysis,
        helper=helper,
        query=query,
    )
    full_tokens = _estimate_tokens(_serialize_payload(full_payload))
    selected_tokens = _estimate_tokens(_serialize_payload(selected_payload, max_chars))
    saved = max(0, full_tokens - selected_tokens)
    pct = round((saved / full_tokens) * 100, 2) if full_tokens else 0.0
    return {
        "full_context_estimated_tokens": full_tokens,
        "selected_context_estimated_tokens": selected_tokens,
        "context_tokens_saved": saved,
        "context_tokens_saved_pct": pct,
    }


def build_project_context(
    *,
    analysis: Any | None = None,
    helper: Any | None = None,
    max_chars: int = CHAT_CONTEXT_MAX_CHARS,
    query: str | None = None,
) -> str | None:
    if not analysis and not helper:
        return None
    compact = _serialize_payload(
        _project_context_payload(analysis=analysis, helper=helper, query=query),
        max_chars,
    )

    return compact or None
