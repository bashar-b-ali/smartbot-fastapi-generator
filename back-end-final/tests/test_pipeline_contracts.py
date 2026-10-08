from __future__ import annotations

from app.llm.file_spec import FileSpec
from app.llm.providers import OllamaProvider
from app.schemas.generation import GenerateFromPromptResponse
from app.services.model_pipeline.artifacts import normalize_artifact_contract
from app.services.model_pipeline.llm_io import adaptive_context_kwargs
from app.services.model_project_pipeline import (
    _coerce_artifact_contract,
    _finalize_contract_data,
    _looks_like_dependency_requirements,
    _normalize_required_file_names,
    _parse_marked_file_specs,
)


def test_dependency_dominated_requirements_are_not_behavior_contracts() -> None:
    requirements = [
        {"id": "R1", "kind": "other", "description": "FastAPI"},
        {"id": "R2", "kind": "other", "description": "SQLAlchemy"},
        {"id": "R3", "kind": "other", "description": "JWT Authentication"},
    ]

    assert _looks_like_dependency_requirements(requirements) is True


def test_behavior_normalization_keeps_route_specific_rules_and_drops_blank_types() -> None:
    contract = normalize_artifact_contract(
        {
            "required_behaviors": [
                {"type": "uniqueness", "table": "enrollments"},
                {
                    "behavior": "role_restricted",
                    "table": "assignments",
                    "method": "POST",
                    "path": "/assignments",
                    "role": "teacher",
                },
                {
                    "behavior": "role_restricted",
                    "table": "assignments",
                    "method": "GET",
                    "path": "/assignments",
                    "role": "student",
                },
            ]
        }
    )

    assert len(contract["required_behaviors"]) == 2
    assert {item["method"] for item in contract["required_behaviors"]} == {"GET", "POST"}


def test_ollama_context_is_adaptive_within_configured_maximum() -> None:
    provider = object.__new__(OllamaProvider)
    provider.num_ctx = 32768

    small = adaptive_context_kwargs(provider, system="Return JSON.", user_payload='{"x":1}', max_tokens=700)
    large = adaptive_context_kwargs(provider, system="Write project.", user_payload="x" * 20_000, max_tokens=12_000)

    assert small == {"num_ctx": 4096, "keep_alive": "24h"}
    assert large == {"num_ctx": 32768, "keep_alive": "24h"}


def test_artifact_contract_accepts_property_and_endpoint_maps() -> None:
    contract = _coerce_artifact_contract(
        {
            "entities": [{"name": "Entity", "properties": [{"name": "field_one"}, "field_two"]}],
            "endpoints": [{"method": "GET", "path": "/entities"}],
            "filters": {"Entity": "field_one"},
        }
    )

    assert contract is not None
    assert {"table": "Entity"} in contract["required_tables"]
    assert {"table": "Entity", "field": "field_one"} in contract["required_fields"]
    assert {"table": "Entity", "field": "field_two"} in contract["required_fields"]
    assert {"method": "GET", "path": "/entities"} in contract["required_routes"]
    assert {"table": "Entity", "field": "field_one"} in contract["required_filters"]


def test_contract_finalization_derives_requirements_from_model_routes() -> None:
    finalized = _finalize_contract_data(
        {
            "summary": "Build a generic API.",
            "requirements": [
                {"type": "FastAPI", "version": "0.110"},
                {"type": "SQLAlchemy", "version": "2"},
            ],
            "routes": [{"method": "GET", "path": "/items"}],
            "artifact_contract": {"summary": "Route checks are top-level."},
        },
        prompt="Build a generic API.",
        source="generation",
    )

    assert finalized["requirements"][0]["kind"] == "artifact_contract"
    assert any(
        route["method"] == "GET" and route["path"] == "/items"
        for route in finalized["artifact_contract"]["required_routes"]
    )


def test_contract_finalization_merges_nested_and_top_level_artifacts() -> None:
    finalized = _finalize_contract_data(
        {
            "summary": "Edit a generic API.",
            "requirements": ["Keep the route available."],
            "routes": [{"method": "GET", "path": "/ping"}],
            "artifact_contract": {"tables": ["widgets"]},
        },
        prompt="Edit a generic API.",
        source="edit",
    )

    assert any(
        route["method"] == "GET" and route["path"] == "/ping"
        for route in finalized["artifact_contract"]["required_routes"]
    )
    assert any(table["table"] == "Widget" for table in finalized["artifact_contract"]["required_tables"])


def test_contract_normalization_expands_route_methods_and_ignores_index_artifacts() -> None:
    finalized = _finalize_contract_data(
        {
            "summary": "Generic API contract.",
            "requirements": ["Implement routes and tables."],
            "artifact_contract": {
                "routes": [{"method": ["GET", "PUT", "DELETE"], "path": "/notes/{note_id}"}],
                "tables": [
                    {"name": "users", "columns": [{"name": "id"}]},
                    {"name": "projects", "columns": [{"name": "id"}]},
                ],
                "indexes": [{"name": "idx_notes_project_id", "table": "notes", "columns": ["project_id"]}],
                "relationships": [{"table": "notes", "columns": ["project_id"], "related_table": "projects"}],
            },
        },
        prompt="Build a generic API.",
        source="generation",
    )

    contract = finalized["artifact_contract"]
    route_methods = {route["method"] for route in contract["required_routes"]}
    assert route_methods == {"GET", "PUT", "DELETE"}
    assert "IdxNotesProjectId" not in {table["table"] for table in contract["required_tables"]}
    assert any(
        rel["from_table"] == "Note" and rel["field"] == "project_id" and rel["to_table"] == "Project"
        for rel in contract["required_relationships"]
    )


def test_marked_file_parser_strips_markdown_fences_from_file_content() -> None:
    files = _parse_marked_file_specs(
        """
### FILE: model.py ###
```python
class User:
    pass
```
### END FILE ###
""".strip()
    )

    assert files[0].path == "model.py"
    assert files[0].content == "class User:\n    pass"


def test_marked_file_parser_strips_unclosed_leading_markdown_fence() -> None:
    files = _parse_marked_file_specs(
        """
### FILE: model.py ###
```python
class User:
    pass
### END FILE ###
""".strip()
    )

    assert files[0].path == "model.py"
    assert files[0].content == "class User:\n    pass"


def test_required_file_name_normalization_maps_package_text_to_requirements() -> None:
    files = _normalize_required_file_names(
        [
            FileSpec(path="main.py", content="from fastapi import FastAPI\napp = FastAPI()\n"),
            FileSpec(path="database.py", content="from sqlmodel import create_engine\nengine = create_engine('sqlite:///x.db')\n"),
            FileSpec(path="code_3.txt", content="fastapi\nsqlmodel\nuvicorn[standard]\n"),
        ]
    )

    assert {item.path for item in files} == {"main.py", "database.py", "requirements.txt"}


def test_generation_response_schema_exposes_pipeline_gate_debug() -> None:
    response = GenerateFromPromptResponse.model_validate(
        {
            "project_id": "project-1",
            "project_root": "/tmp/project",
            "plan": {},
            "files": [{"path": "main.py", "bytes_written": 12}],
            "all_files": [{"path": "main.py", "bytes_written": 12}],
            "stats": {
                "total_files": 1,
                "total_bytes": 12,
                "input_tokens": 1,
                "output_tokens": 1,
                "retries": 0,
            },
            "provider": "scripted-generation/test",
            "selected_template": {"key": "model_owned"},
            "validation": {
                "passed": False,
                "accepted": False,
                "static_safe": True,
                "write_allowed": True,
                "pipeline_state": "draft_written",
                "failure_category": "missing_artifact",
                "checks": [{"path": "main.py", "passed": True, "error": ""}],
                "missing_required": [],
                "artifact_validation": {"passed": False, "missing_artifacts": ["R1 missing route GET /items"]},
                "missing_artifacts": ["R1 missing route GET /items"],
                "regressions": [],
                "model_generation": {"trace": [{"stage": "generation.validation.initial", "status": "static_safe"}]},
            },
            "artifact_validation": {"passed": False},
            "accepted": False,
            "pipeline_state": "draft_written",
            "failure_category": "missing_artifact",
            "missing_artifacts": ["R1 missing route GET /items"],
            "regressions": [],
            "pipeline_trace": [{"stage": "generation.validation.initial", "status": "static_safe"}],
            "stage_timings_ms": {"validation": 1.0},
            "context_selection": {},
            "spec_summary": {"resource_count": 1},
            "draft_reason": "static-safe draft has missing artifacts",
            "runtime_validation": {"skipped": True},
            "working_summary": ["Wrote static-safe model-owned FastAPI project files from the prompt."],
            "next_prompt": "Do you want to view `main.py`?",
            "view_file_path": "main.py",
        }
    )

    assert response.validation.static_safe is True
    assert response.validation.write_allowed is True
    assert response.pipeline_state == "draft_written"
    assert response.failure_category == "missing_artifact"
    assert response.validation.model_generation["trace"][0]["status"] == "static_safe"
    assert response.pipeline_trace[0]["stage"] == "generation.validation.initial"
    assert response.spec_summary["resource_count"] == 1
    assert response.draft_reason == "static-safe draft has missing artifacts"


def test_contract_finalization_falls_back_to_prompt_intent_for_notes_crud() -> None:
    prompt = (
        "Build a FastAPI backend for a notes app. It should manage notes with title, body, "
        "and pinned fields. Include create, list, detail, update, and delete endpoints. "
        "Let list notes filter by pinned."
    )

    finalized = _finalize_contract_data(
        {"summary": "Model omitted usable requirements.", "requirements": []},
        prompt=prompt,
        source="generation",
    )

    contract = finalized["artifact_contract"]
    assert any(table["table"] == "Note" for table in contract["required_tables"])
    assert any(field["table"] == "Note" and field["field"] == "title" for field in contract["required_fields"])
    assert any(field["table"] == "Note" and field["field"] == "body" for field in contract["required_fields"])
    assert any(field["table"] == "Note" and field["field"] == "pinned" for field in contract["required_fields"])
    assert any(field["table"] == "Note" and field["field"] == "pinned" for field in contract["required_filters"])
    assert any(route["method"] == "DELETE" and route["path"] == "/notes/{note_id}" for route in contract["required_routes"])
