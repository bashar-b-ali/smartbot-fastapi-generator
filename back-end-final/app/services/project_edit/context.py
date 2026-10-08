from __future__ import annotations

import json
from typing import Any


def compact_edit_context(index: dict[str, Any], prompt: str, target_paths: list[str]) -> dict[str, Any]:
    selected = set(target_paths)
    route_terms = {
        part
        for route in index.get("api_routes") or []
        for part in str(route.get("path") or "").strip("/").split("/")
        if part
    }
    prompt_lower = (prompt or "").lower()
    matched_terms = {term for term in route_terms if term.lower() in prompt_lower}

    def include_by_file(item: dict[str, Any]) -> bool:
        return str(item.get("file") or item.get("path") or "") in selected

    routes = [
        route
        for route in index.get("api_routes") or []
        if include_by_file(route)
        or any(term and term.lower() in str(route.get("path") or "").lower() for term in matched_terms)
    ]
    tables = [
        table
        for table in (index.get("database_schema") or {}).get("tables") or []
        if include_by_file(table)
    ]
    functions = [
        fn
        for fn in index.get("function_summaries") or []
        if include_by_file(fn)
    ]
    files = [
        file
        for file in index.get("file_index") or []
        if str(file.get("path") or "") in selected or file.get("parse_error")
    ]
    return {
        "request": prompt,
        "selected_files": target_paths,
        "file_index": files[:30],
        "api_routes": routes[:40],
        "database_schema": {"tables": tables[:30]},
        "function_summaries": functions[:40],
    }


def compact_project_state(
    index: dict[str, Any],
    *,
    max_items: int = 100,
    requirement_contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    valid_contracts = [
        contract
        for contract in (requirement_contracts or [])
        if isinstance(contract, dict)
        and contract.get("accepted") is True
    ]
    return {
        "file_index": (index.get("file_index") or [])[:max_items],
        "api_routes": (index.get("api_routes") or [])[:max_items],
        "database_schema": index.get("database_schema") or {},
        "function_summaries": (index.get("function_summaries") or [])[:max_items],
        "requirement_contracts": valid_contracts[:8],
    }


def estimated_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return max(1, (len(text) + 3) // 4) if text else 0


def edit_context_savings(index: dict[str, Any], prompt: str, target_paths: list[str]) -> dict[str, int | float]:
    full_tokens = estimated_tokens(compact_project_state(index, max_items=10_000))
    selected_tokens = estimated_tokens(compact_edit_context(index, prompt, target_paths))
    selected_tokens = min(selected_tokens, full_tokens) if full_tokens else selected_tokens
    saved = max(0, full_tokens - selected_tokens)
    pct = round((saved / full_tokens) * 100, 2) if full_tokens else 0.0
    return {
        "full_context_estimated_tokens": full_tokens,
        "selected_context_estimated_tokens": selected_tokens,
        "context_tokens_saved": saved,
        "context_tokens_saved_pct": pct,
    }
