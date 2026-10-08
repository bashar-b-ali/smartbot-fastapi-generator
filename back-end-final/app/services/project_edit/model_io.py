from __future__ import annotations

import json
import re
from typing import Any

from app.core.exceptions import ValidationError


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValidationError("edit model did not return JSON") from exc
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as inner:
            raise ValidationError("edit model returned invalid JSON") from inner
    if not isinstance(parsed, dict):
        raise ValidationError("edit model JSON must be an object")
    return parsed


def model_file_items(model_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept common JSON shapes returned by smaller local models."""
    files = model_result.get("files")
    if isinstance(files, list):
        return [item for item in files if isinstance(item, dict)]
    if isinstance(files, dict):
        return [
            {"path": str(path), "content": content}
            for path, content in files.items()
            if isinstance(content, str)
        ]

    changes = model_result.get("changes")
    if isinstance(changes, list):
        return [item for item in changes if isinstance(item, dict)]

    path = model_result.get("path") or model_result.get("file") or model_result.get("filename")
    content = model_result.get("content") or model_result.get("code")
    if isinstance(path, str) and isinstance(content, str):
        return [{"path": path, "content": content}]

    return []


def item_path(item: dict[str, Any]) -> str:
    return str(item.get("path") or item.get("file") or item.get("filename") or "")


def item_content(item: dict[str, Any]) -> Any:
    return item.get("content") if "content" in item else item.get("code")


def model_item_debug(model_result: dict[str, Any]) -> list[dict[str, Any]]:
    debug: list[dict[str, Any]] = []
    for item in model_file_items(model_result):
        content = item_content(item)
        debug.append(
            {
                "path": item_path(item),
                "content_chars": len(content) if isinstance(content, str) else 0,
                "has_content": isinstance(content, str) and bool(content.strip()),
            }
        )
    return debug[:20]


def model_result_debug(model_result: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key in ("status", "message", "error", "missing_info"):
        value = model_result.get(key)
        if value:
            data[key] = value
    return data
