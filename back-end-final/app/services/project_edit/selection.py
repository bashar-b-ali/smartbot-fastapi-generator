from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.exceptions import ValidationError
from app.services.model_pipeline.paths import is_safe_file_path
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.project_edit.artifact_validation import artifact_file_hints
from app.services.project_edit.io import safe_rel

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]*|[0-9]+")


def explicit_project_paths(prompt: str) -> list[str]:
    paths: list[str] = []
    for explicit in re.findall(r"[A-Za-z0-9_.@/-]+\.(?:py|md|txt|json|yaml|yml|toml|env|sql)", prompt or ""):
        try:
            rel = safe_rel(explicit)
        except ValidationError:
            continue
        if rel not in paths:
            paths.append(rel)
    return paths


def _split_identifier(value: Any) -> list[str]:
    expanded = re.sub(r"(?<!^)(?=[A-Z])", "_", str(value or ""))
    return [match.group(0).lower() for match in TOKEN_RE.finditer(expanded)]


def _term_variants(term: str) -> set[str]:
    if not term:
        return set()
    terms = {term}
    if term.endswith("ies") and len(term) > 3:
        terms.add(term[:-3] + "y")
    elif term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        terms.add(term[:-1])
    if term.endswith("y") and len(term) > 1 and term[-2] not in "aeiou":
        terms.add(term[:-1] + "ies")
    elif term.endswith(("s", "x", "z", "ch", "sh")):
        terms.add(term + "es")
    else:
        terms.add(term + "s")
    return terms


def _tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    for token in _split_identifier(value):
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.update(_term_variants(token))
    return tokens


def _path_exists(root: Path, path: str) -> bool:
    return bool(path) and (root / path).exists()


def _editable_files(index: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for file_info in index.get("file_index") or []:
        path = str(file_info.get("path") or "")
        if is_safe_file_path(path) and path not in paths:
            paths.append(path)
    return paths


def _add_score(scores: dict[str, float], root: Path, path: str, points: float) -> None:
    if not path or not _path_exists(root, path):
        return
    scores[path] = scores.get(path, 0.0) + points


def _overlap_score(prompt_terms: set[str], value: Any) -> int:
    return len(prompt_terms & _tokens(value))


def _related_module_paths(root: Path, selected: list[str]) -> list[str]:
    related: list[str] = []
    selected_set = set(selected)
    for path in selected:
        rel = Path(path)
        if rel.suffix != ".py":
            continue
        stem = rel.stem
        candidates = []
        if rel.parent == Path("."):
            candidates.append(f"routers/{stem}.py")
        elif rel.parent.as_posix() == "routers":
            candidates.append(f"{stem}.py")
        for candidate in candidates:
            if candidate not in selected_set and _path_exists(root, candidate):
                related.append(candidate)
                selected_set.add(candidate)
    return related


def _append_context_files(root: Path, selected: list[str]) -> list[str]:
    context = list(selected)
    entrypoint = FASTAPI_PROFILE.entrypoint_file
    if selected and entrypoint not in context and _path_exists(root, entrypoint) and len(context) < 10:
        context.append(entrypoint)
    for related in _related_module_paths(root, context):
        if len(context) >= 10:
            break
        if related not in context:
            context.append(related)
    return context


def select_edit_files(
    root: Path,
    index: dict[str, Any],
    prompt: str,
    *,
    requirement_contracts: list[dict[str, Any]] | None = None,
) -> list[str]:
    prompt_terms = _tokens(prompt)
    editable_files = _editable_files(index)
    selected: list[str] = []

    for path in artifact_file_hints(index, requirement_contracts or []):
        if path in editable_files or _path_exists(root, path):
            selected.append(path)

    for rel in explicit_project_paths(prompt or ""):
        if rel in editable_files or _path_exists(root, rel):
            selected.append(rel)

    if selected:
        return _append_context_files(root, list(dict.fromkeys(selected)))[:10]

    scores: dict[str, float] = {}
    for file_info in index.get("file_index") or []:
        path = str(file_info.get("path") or "")
        if not is_safe_file_path(path):
            continue
        overlap = _overlap_score(prompt_terms, path)
        for key in ("tables", "routes", "classes", "class_fields", "imports"):
            overlap += _overlap_score(prompt_terms, " ".join(map(str, file_info.get(key) or [])))
        if overlap:
            _add_score(scores, root, path, 2 + overlap)

    for table in (index.get("database_schema") or {}).get("tables") or []:
        path = str(table.get("file") or "")
        overlap = _overlap_score(prompt_terms, table.get("name"))
        for field in table.get("fields") or []:
            overlap += _overlap_score(prompt_terms, field.get("name"))
            overlap += _overlap_score(prompt_terms, field.get("foreign_key"))
        if overlap:
            _add_score(scores, root, path, 8 + (overlap * 3))

    for class_info in index.get("class_summaries") or []:
        path = str(class_info.get("file") or "")
        overlap = _overlap_score(prompt_terms, class_info.get("name"))
        for field in class_info.get("fields") or []:
            overlap += _overlap_score(prompt_terms, field.get("name"))
        if overlap:
            _add_score(scores, root, path, 5 + (overlap * 2))

    for route in index.get("api_routes") or []:
        path = str(route.get("file") or "")
        route_terms = " ".join(
            [
                str(route.get("method") or ""),
                str(route.get("path") or ""),
                str(route.get("function") or ""),
                " ".join(str(param.get("name") or "") for param in route.get("parameters") or []),
                " ".join(route.get("body_schema_names") or []),
            ]
        )
        overlap = _overlap_score(prompt_terms, route_terms)
        if overlap:
            _add_score(scores, root, path, 6 + (overlap * 2))

    for fn in index.get("function_summaries") or []:
        path = str(fn.get("file") or "")
        overlap = _overlap_score(
            prompt_terms,
            " ".join(
                [
                    str(fn.get("name") or ""),
                    str(fn.get("signature") or ""),
                    str(fn.get("summary") or ""),
                    " ".join(map(str, fn.get("body_symbols") or [])),
                ]
            ),
        )
        if overlap:
            _add_score(scores, root, path, 3 + overlap)

    for item in index.get("query_filters") or []:
        path = str(item.get("file") or "")
        overlap = _overlap_score(
            prompt_terms,
            " ".join(str(item.get(key) or "") for key in ("function", "table", "field", "value")),
        )
        if overlap:
            _add_score(scores, root, path, 3 + overlap)

    ordered_scores = sorted(
        scores.items(),
        key=lambda item: (-item[1], editable_files.index(item[0]) if item[0] in editable_files else 10_000, item[0]),
    )
    if ordered_scores and ordered_scores[0][1] >= 8:
        ordered_scores = [item for item in ordered_scores if item[1] >= 6]
    selected = [path for path, _score in ordered_scores]

    if not selected:
        selected = [path for path in editable_files if _path_exists(root, path)]

    selected = _append_context_files(root, selected[:10])
    return list(dict.fromkeys(path for path in selected if _path_exists(root, path)))[:10]
