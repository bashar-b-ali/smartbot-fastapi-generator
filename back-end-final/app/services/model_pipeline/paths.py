from __future__ import annotations

import re
from pathlib import Path

from app.core.exceptions import ValidationError
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.project_edit.io import safe_rel

PLACEHOLDER_MARKERS = {
    "FULL replacement content",
    "FULL file content",
    "existing/path.py",
    "relative/path.py",
}
WRITABLE_SUFFIXES = {".py", ".txt", ".md", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sql"}
REQUIRED_PROJECT_FILES = set(FASTAPI_PROFILE.required_files)
CANONICAL_ENTRY_FILENAMES = set(FASTAPI_PROFILE.canonical_entry_filenames)


def is_safe_file_path(path: str) -> bool:
    try:
        rel = safe_rel(path)
    except ValidationError:
        return False
    if rel == FASTAPI_PROFILE.endpoint_doc_file or rel.startswith(("tests/", "docs/")):
        return False
    return Path(rel).suffix.lower() in WRITABLE_SUFFIXES or Path(rel).name == FASTAPI_PROFILE.dependency_file


def canonical_project_path(path: str) -> str:
    rel = safe_rel(path)
    name = Path(rel).name
    if name in CANONICAL_ENTRY_FILENAMES:
        return name
    return rel


def assert_not_placeholder(value: str, *, label: str) -> None:
    for marker in PLACEHOLDER_MARKERS:
        if marker in value:
            raise ValidationError(f"{label} contains placeholder marker `{marker}`")


def looks_like_requirements_content(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines or len(lines) > 40:
        return False
    if any((" " in line and not any(marker in line for marker in ("==", ">=", "<=", "~=", "[", "]"))) for line in lines):
        return False
    return all(re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]=?[^\\s]+)?", line) for line in lines)
