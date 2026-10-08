from __future__ import annotations

from typing import Any

from app.services.model_pipeline.naming import identifier, plural

ARTIFACT_KEYS = (
    "required_routes",
    "required_tables",
    "required_fields",
    "required_filters",
    "required_relationships",
    "required_behaviors",
    "removed_artifacts",
)


def normalize_artifact_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {}

    normalized: dict[str, Any] = {
        key: value
        for key, value in contract.items()
        if key not in ARTIFACT_KEYS and key != "custom_routes"
    }
    for key in ARTIFACT_KEYS:
        items: list[dict[str, Any]] = []
        for item in contract.get(key) or []:
            if isinstance(item, dict):
                normalized_item = _normalize_artifact_item(key, item)
                if key == "required_behaviors" and not normalized_item.get("behavior"):
                    continue
                _append_unique(items, normalized_item, _marker_keys(key))
        if items:
            normalized[key] = items

    custom_routes: list[dict[str, Any]] = []
    for item in contract.get("custom_routes") or []:
        if isinstance(item, dict):
            _append_unique(custom_routes, normalize_custom_route(item), ("kind", "method", "path"))
    if custom_routes:
        normalized["custom_routes"] = custom_routes
    return normalized


def normalize_custom_route(route: dict[str, Any]) -> dict[str, Any]:
    out = {
        key: value
        for key, value in route.items()
        if value not in (None, "", [])
    }
    if out.get("kind"):
        out["kind"] = identifier(out["kind"])
    if out.get("method"):
        out["method"] = str(out["method"]).upper()
    if out.get("path"):
        out["path"] = canonical_path(out["path"])
    for key in ("table", "parent_table", "child_table"):
        if out.get(key):
            out[key] = plural(out[key])
    for key in ("required_tables", "required_fields", "required_filters", "required_relationships", "required_behaviors"):
        if isinstance(out.get(key), list):
            out[key] = [
                _normalize_artifact_item(key, item)
                for item in out[key]
                if isinstance(item, dict)
            ]
    return out


def canonical_path(path: Any) -> str:
    value = str(path or "").strip().rstrip(".,;")
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    parts: list[str] = []
    for raw_part in value.strip("/").split("/"):
        if not raw_part:
            continue
        if raw_part.startswith("{") and raw_part.endswith("}"):
            parts.append("{" + identifier(raw_part[1:-1]) + "}")
        else:
            parts.append(_normalize_path_segment(raw_part))
    normalized = "/" + "/".join(parts)
    return normalized.rstrip("/") if len(normalized) > 1 else normalized


def _normalize_path_segment(segment: str) -> str:
    if "-" in segment:
        return "-".join(identifier(part) for part in segment.split("-") if part)
    if segment in {"costumer", "costumers"}:
        return plural(segment)
    return segment


def _normalize_artifact_item(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    out = {
        key: value
        for key, value in item.items()
        if value not in (None, "", [])
    }
    if kind == "required_routes":
        out["method"] = str(out.get("method") or "*").upper()
        out["path"] = canonical_path(out.get("path"))
    elif kind == "required_tables":
        out["table"] = plural(out.get("table") or out.get("name"))
    elif kind in {"required_fields", "required_filters"}:
        out["table"] = plural(out.get("table") or out.get("model") or out.get("class"))
        out["field"] = identifier(out.get("field") or out.get("name"), numeric_prefix="field")
    elif kind == "required_relationships":
        out["from_table"] = plural(out.get("from_table") or out.get("table"))
        out["to_table"] = plural(
            out.get("to_table")
            or out.get("target_table")
            or out.get("related_table")
            or out.get("references_table")
            or out.get("to")
        )
        columns = out.get("columns")
        first_column = columns[0] if isinstance(columns, list) and columns else ""
        out["field"] = identifier(out.get("field") or out.get("column") or out.get("name") or first_column, numeric_prefix="field")
    elif kind == "required_behaviors":
        out["behavior"] = identifier(out.get("behavior") or out.get("name") or out.get("kind"))
        if out.get("table"):
            out["table"] = plural(out["table"])
    elif kind == "removed_artifacts":
        out["kind"] = identifier(out.get("kind") or "artifact")
        if out.get("table"):
            out["table"] = plural(out["table"])
        if out.get("field"):
            out["field"] = identifier(out["field"], numeric_prefix="field")
        if out.get("path"):
            out["path"] = str(out["path"]).replace("\\", "/").strip("/")
    return out


def _append_unique(target: list[dict[str, Any]], item: dict[str, Any], keys: tuple[str, ...]) -> None:
    if not item:
        return
    marker = tuple(str(item.get(key) or "").lower() for key in keys)
    if not any(tuple(str(existing.get(key) or "").lower() for key in keys) == marker for existing in target):
        target.append(item)


def _marker_keys(key: str) -> tuple[str, ...]:
    if key == "required_routes":
        return ("method", "path")
    if key in {"required_fields", "required_filters"}:
        return ("table", "field")
    if key == "required_relationships":
        return ("from_table", "field", "to_table")
    if key == "required_behaviors":
        return ("behavior", "table", "method", "path", "role")
    if key == "removed_artifacts":
        return ("kind", "table", "field", "path")
    return ("table",)
