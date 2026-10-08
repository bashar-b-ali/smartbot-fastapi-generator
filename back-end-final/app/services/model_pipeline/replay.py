from __future__ import annotations

from typing import Any

from app.llm.file_spec import FileSpec
from app.services import model_project_pipeline as _common
from app.services.model_pipeline.validation import validate_file_specs

_parse_marked_file_specs = _common._parse_marked_file_specs
_normalize_required_file_names = _common._normalize_required_file_names


def _trace_strings(stage_outputs: dict[str, Any]) -> list[tuple[str, str]]:
    strings: list[tuple[str, str]] = []
    for event in stage_outputs.get("trace") or []:
        if not isinstance(event, dict):
            continue
        stage = str(event.get("stage") or "trace")
        for key in ("raw_response", "raw_content", "text", "content", "raw_response_excerpt"):
            value = event.get(key)
            if isinstance(value, str) and "### FILE:" in value:
                strings.append((stage, value))
    for key in ("model_generation", "complete_project", "repair"):
        value = stage_outputs.get(key)
        if isinstance(value, str) and "### FILE:" in value:
            strings.append((key, value))
        if isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    for text_key in ("raw_response", "content", "text"):
                        text = item.get(text_key)
                        if isinstance(text, str) and "### FILE:" in text:
                            strings.append((f"{key}[{idx}]", text))
    return strings


def replay_stage_outputs(stage_outputs: dict[str, Any], artifact_contract: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for source, text in _trace_strings(stage_outputs):
        files = _normalize_required_file_names(_parse_marked_file_specs(text))
        if not files:
            continue
        validation = validate_file_specs(files, artifact_contract)
        candidates.append(
            {
                "source": source,
                "files": [{"path": item.path, "chars": len(item.content)} for item in files],
                "validation": validation,
            }
        )

    if not candidates:
        return {
            "replayable": False,
            "candidate_count": 0,
            "reason": "stored pipeline run does not contain complete file-wrapper outputs; only summaries/excerpts may be available",
            "candidates": [],
        }

    accepted = [item for item in candidates if item["validation"].get("accepted")]
    static_safe = [item for item in candidates if item["validation"].get("static_safe")]
    selected = (accepted or static_safe or candidates)[0]
    return {
        "replayable": True,
        "candidate_count": len(candidates),
        "selected_source": selected["source"],
        "accepted": bool(selected["validation"].get("accepted")),
        "failure_category": selected["validation"].get("failure_category"),
        "candidates": candidates,
    }


def replay_file_specs(files: list[FileSpec], artifact_contract: dict[str, Any]) -> dict[str, Any]:
    validation = validate_file_specs(files, artifact_contract)
    return {
        "replayable": True,
        "candidate_count": 1,
        "selected_source": "provided_files",
        "accepted": bool(validation.get("accepted")),
        "failure_category": validation.get("failure_category"),
        "candidates": [
            {
                "source": "provided_files",
                "files": [{"path": item.path, "chars": len(item.content)} for item in files],
                "validation": validation,
            }
        ],
    }
