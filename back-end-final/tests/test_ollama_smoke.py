from __future__ import annotations

import os

import pytest
from pipeline_fixtures import minimal_project

from app.llm.providers import OllamaProvider
from app.services.model_pipeline.editing import ModelPatchEditPipeline
from app.services.model_pipeline.generation import ModelOwnedGenerationPipeline
from app.services.project_indexer import build_project_index

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OLLAMA_SMOKE") != "1",
    reason="Set RUN_OLLAMA_SMOKE=1 to run live Ollama pipeline smoke tests.",
)


def test_ollama_generation_smoke_task_tracker_contract() -> None:
    provider = OllamaProvider(model=os.getenv("OLLAMA_SMOKE_MODEL") or None)
    result = ModelOwnedGenerationPipeline(provider).preview(
        "Build a FastAPI backend for a task tracker with notes, tasks, and projects. "
        "Protect resource endpoints with auth. Include create, list, detail, update endpoints."
    )

    assert result.requirements
    assert result.artifact_contract.get("required_routes") or result.artifact_contract.get("required_tables")


def test_ollama_invalid_json_recovery_smoke() -> None:
    provider = OllamaProvider(model=os.getenv("OLLAMA_SMOKE_MODEL") or None)
    result = ModelOwnedGenerationPipeline(provider).plan_schema(
        "Build a compact FastAPI backend for a notes API. Return a valid schema plan."
    )

    assert result[0]["validation"]["passed"] is True


def test_ollama_edit_smoke_existing_project(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = OllamaProvider(model=os.getenv("OLLAMA_SMOKE_MODEL") or None)
    result = ModelPatchEditPipeline(provider).edit(
        prompt="Change the /ping response status value to live-smoke while keeping the route available.",
        root=tmp_path,
        index=build_project_index(tmp_path),
    )

    assert result.changed
