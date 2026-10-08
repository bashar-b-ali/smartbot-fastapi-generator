from __future__ import annotations

import json

import pytest
from pipeline_fixtures import (
    ScriptedProvider,
    edit_contract_json,
    minimal_project,
    no_schema_delta_json,
    write,
)

from app.core.exceptions import ValidationError
from app.services.model_pipeline.editing import (
    ModelPatchEditPipeline,
    _deterministic_api_delta_from_intent,
    _deterministic_schema_delta_from_intent,
    apply_patch_set,
)
from app.services.model_pipeline.intent import extract_prompt_intent
from app.services.model_pipeline.renderer import render_fastapi_sqlmodel_project
from app.services.model_pipeline.types import ModelPatchValidationError, ModelRunUsage
from app.services.project_indexer import build_project_index


class NonScriptedProvider:
    model = "live-like"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("the deterministic renderer should not call the provider")


def test_patch_edit_applies_exact_model_patch(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(),
            json.dumps(
                {
                    "summary": "Update response.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": 'return {"status": "updated"}',
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Change the response text.",
        root=tmp_path,
        index=build_project_index(tmp_path),
    )

    assert [item.path for item in result.changed] == ["main.py"]
    assert result.stage_outputs["trace"][-1]["status"] == "accepted"


def test_patch_edit_rejects_static_safe_edit_with_current_artifact_gap(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(route="/not-yet-present"),
            json.dumps(
                {
                    "summary": "Update response.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": 'return {"status": "static-safe-gap"}',
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    before = (tmp_path / "main.py").read_text(encoding="utf-8")
    with pytest.raises(ModelPatchValidationError):
        ModelPatchEditPipeline(provider).edit(
            prompt="Change the response text.",
            root=tmp_path,
            index=build_project_index(tmp_path),
        )

    assert (tmp_path / "main.py").read_text(encoding="utf-8") == before


def test_api_delta_uses_strict_repair_when_model_echoes_payload(tmp_path) -> None:
    minimal_project(tmp_path)
    echoed_payload = json.dumps(
        {
            "request": "Change the API.",
            "saved_pipeline_memory": {},
            "previous_response": {"summary": "wrong shape"},
            "error": "model contract did not produce usable requirements",
        }
    )
    provider = ScriptedProvider(
        [
            json.dumps({"summary": "Wrong shape.", "no_schema_change": True, "ddl_sql": [], "schema": {}}),
            echoed_payload,
            edit_contract_json(route="/items"),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta = json.loads(no_schema_delta_json())
    contract, usage = pipeline.plan_api_delta(
        prompt="Add item routes.",
        index=build_project_index(tmp_path),
        schema_delta=schema_delta,
    )

    assert usage.retries == 2
    assert any(event["stage"] == "edit.api_delta_repair_strict" for event in pipeline.trace)
    assert any(route["path"] == "/items" for route in contract["required_routes"])


def test_schema_delta_uses_strict_repair_when_model_echoes_keys(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Bad schema.",
                    "no_schema_change": False,
                    "ddl_sql": [],
                    "schema": {"tables": [{"name": "items", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["items"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
            json.dumps({"summary": "Bad repair.", "keys": ["summary", "schema"], "validation_errors": []}),
            json.dumps(
                {
                    "summary": "Add items table.",
                    "no_schema_change": False,
                    "ddl_sql": ["CREATE TABLE items (id INTEGER PRIMARY KEY);"],
                    "schema": {"tables": [{"name": "items", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["items"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta, usage = pipeline.plan_schema_delta(
        prompt="Add items.",
        index=build_project_index(tmp_path),
    )

    assert usage.retries == 2
    assert any(event["stage"] == "edit.schema_delta_repair_strict" for event in pipeline.trace)
    assert schema_delta["validation"]["passed"] is True
    assert schema_delta["schema"]["tables"][0]["name"] == "items"


def test_schema_delta_rejects_no_change_with_affected_tables(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Contradictory no-change response.",
                    "no_schema_change": True,
                    "ddl_sql": None,
                    "schema": {"tables": [{"name": "Event", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["Event"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
            json.dumps({"summary": "Bad repair.", "keys": ["summary"], "validation_errors": []}),
            json.dumps(
                {
                    "summary": "Add posts table.",
                    "no_schema_change": False,
                    "ddl_sql": ["CREATE TABLE posts (id INTEGER PRIMARY KEY);"],
                    "schema": {"tables": [{"name": "posts", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["posts"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta, _usage = pipeline.plan_schema_delta(
        prompt="Add posts.",
        index=build_project_index(tmp_path),
    )

    assert any(event["stage"] == "edit.schema_delta_repair_strict" for event in pipeline.trace)
    assert schema_delta["no_schema_change"] is False
    assert schema_delta["schema"]["tables"][0]["name"] == "posts"


def test_schema_delta_repairs_targeted_no_change_contradiction_without_model_retry(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Add price to orders.",
                    "no_schema_change": True,
                    "ddl_sql": "",
                    "schema": {"tables": [{"name": "orders", "columns": [{"name": "price", "type": "NUMERIC"}]}]},
                    "affected_tables": ["orders"],
                    "affected_fields": ["orders.price"],
                    "assumptions": [],
                }
            ),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta, usage = pipeline.plan_schema_delta(
        prompt="Add price to orders.",
        index=build_project_index(tmp_path),
    )

    trace_stages = [event["stage"] for event in pipeline.trace]
    assert usage.retries == 0
    assert "edit.schema_delta_deterministic_repair" in trace_stages
    assert "edit.schema_delta_repair" not in trace_stages
    assert schema_delta["no_schema_change"] is False
    assert schema_delta["validation"]["passed"] is True


def test_schema_delta_refocuses_when_strict_repair_copies_existing_schema(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Bad schema.",
                    "no_schema_change": False,
                    "ddl_sql": [],
                    "schema": {"tables": [{"name": "events", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["events"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
            json.dumps({"summary": "Bad repair.", "keys": ["summary", "schema"], "validation_errors": []}),
            json.dumps(
                {
                    "summary": "Still copied existing schema.",
                    "no_schema_change": False,
                    "ddl_sql": ["CREATE TABLE events (id INTEGER PRIMARY KEY);"],
                    "schema": {"tables": [{"name": "Event", "columns": [{"name": "id"}]}]},
                    "affected_tables": ["Event"],
                    "affected_fields": ["id"],
                    "assumptions": [],
                }
            ),
            json.dumps(
                {
                    "summary": "Add posts table.",
                    "no_schema_change": False,
                    "ddl_sql": ["CREATE TABLE posts (id INTEGER PRIMARY KEY, status TEXT);"],
                    "schema": {"tables": [{"name": "posts", "columns": [{"name": "id"}, {"name": "status"}]}]},
                    "affected_tables": ["posts"],
                    "affected_fields": ["id", "status"],
                    "assumptions": [],
                }
            ),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta, usage = pipeline.plan_schema_delta(
        prompt="Add posts.",
        index=build_project_index(tmp_path),
    )

    assert usage.retries == 3
    assert any(event["stage"] == "edit.schema_delta_repair_refocus" for event in pipeline.trace)
    assert schema_delta["validation"]["passed"] is True
    assert schema_delta["schema"]["tables"][0]["name"] == "posts"


def test_prompt_intent_normalizes_customer_typo_for_custom_routes() -> None:
    intent = extract_prompt_intent(
        "I want an api to get the top 10 costumers that have orders then paginate orders for each costumer"
    )

    routes = intent["custom_routes"]
    assert routes[0]["parent_table"] == "customers"
    assert routes[1]["parent_table"] == "customers"
    assert routes[0]["path"] == "/customers/top-by-orders"
    assert routes[1]["path"] == "/customers/{customer_id}/orders"


def test_api_delta_includes_schema_delta_artifacts(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider([edit_contract_json(route="/posts")])
    schema_delta = {
        "summary": "Add posts.",
        "no_schema_change": False,
        "ddl_sql": ["CREATE TABLE posts (id INTEGER PRIMARY KEY, status TEXT);"],
        "schema": {"tables": [{"name": "posts", "columns": [{"name": "id"}, {"name": "status", "filter": True}]}]},
        "affected_tables": ["posts"],
        "affected_fields": ["id", "status"],
        "assumptions": [],
        "validation": {"passed": True},
    }

    contract, _usage = ModelPatchEditPipeline(provider).plan_api_delta(
        prompt="Add posts.",
        index=build_project_index(tmp_path),
        schema_delta=schema_delta,
    )

    assert {"table": "posts"} in contract["required_tables"]
    assert {"table": "posts", "field": "id"} in contract["required_fields"]
    assert {"table": "posts", "field": "status"} in contract["required_filters"]


def test_schema_delta_rejects_unmentioned_existing_tables(tmp_path) -> None:
    write(
        tmp_path / "database.py",
        """
from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
""".lstrip(),
    )
    write(tmp_path / "main.py", "from fastapi import FastAPI\n\napp = FastAPI()\n")
    write(tmp_path / "requirements.txt", "fastapi\nsqlmodel\n")
    stale_schema = {
        "summary": "Add posts.",
        "no_schema_change": False,
        "ddl_sql": [
            "CREATE TABLE event (id INTEGER PRIMARY KEY, title TEXT);",
            "CREATE TABLE post (id INTEGER PRIMARY KEY, status TEXT);",
        ],
        "schema": {
            "tables": [
                {"name": "event", "fields": [{"name": "id"}, {"name": "title"}]},
                {"name": "post", "fields": [{"name": "id"}, {"name": "status"}]},
            ]
        },
        "affected_tables": ["event", "post"],
        "affected_fields": ["event.id", "post.status"],
        "assumptions": [],
    }
    focused_schema = {
        **stale_schema,
        "ddl_sql": ["CREATE TABLE post (id INTEGER PRIMARY KEY, status TEXT);"],
        "schema": {"tables": [{"name": "post", "fields": [{"name": "id"}, {"name": "status"}]}]},
        "affected_tables": ["post"],
        "affected_fields": ["post.id", "post.status"],
    }
    provider = ScriptedProvider(
        [
            json.dumps(stale_schema),
            json.dumps(stale_schema),
            json.dumps(stale_schema),
            json.dumps(stale_schema),
            json.dumps(focused_schema),
        ]
    )
    pipeline = ModelPatchEditPipeline(provider)

    schema_delta, _usage = pipeline.plan_schema_delta(
        prompt="Build a FastAPI backend for a blog. It should manage posts.",
        index=build_project_index(tmp_path),
    )

    assert any(event["stage"] == "edit.schema_delta_repair_user_focus" for event in pipeline.trace)
    assert schema_delta["schema"]["tables"] == focused_schema["schema"]["tables"]


def test_api_delta_refocuses_routes_to_schema_delta_tables(tmp_path) -> None:
    minimal_project(tmp_path)
    schema_delta = {
        "summary": "Add posts.",
        "no_schema_change": False,
        "ddl_sql": ["CREATE TABLE post (id INTEGER PRIMARY KEY, status TEXT);"],
        "schema": {"tables": [{"name": "post", "fields": [{"name": "id"}, {"name": "status"}]}]},
        "affected_tables": ["post"],
        "affected_fields": ["post.id", "post.status"],
        "assumptions": [],
        "validation": {"passed": True},
    }
    provider = ScriptedProvider([edit_contract_json(route="/events"), edit_contract_json(route="/posts")])
    pipeline = ModelPatchEditPipeline(provider)

    contract, _usage = pipeline.plan_api_delta(
        prompt="Build a FastAPI backend for a blog. It should manage posts.",
        index=build_project_index(tmp_path),
        schema_delta=schema_delta,
    )

    assert any(event["stage"] == "edit.api_delta_repair_schema_focus" for event in pipeline.trace)
    assert any(route["path"] == "/posts" for route in contract["required_routes"])


def test_edit_renderer_uses_current_index_when_schema_memory_is_missing(tmp_path) -> None:
    schema_plan = {
        "domain": "blog",
        "ddl_sql": "CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT);",
        "schema": {"tables": [{"name": "posts", "columns": [{"name": "id"}, {"name": "title", "type": "TEXT"}]}]},
        "validation": {"passed": True},
        "assumptions": [],
    }
    api_contract = {
        "summary": "Blog API.",
        "requirements": [{"id": "R1", "description": "Manage posts."}],
        "routes": [{"method": "GET", "path": "/posts"}],
        "auth": {},
        "websocket_events": [],
        "artifact_contract": {
            "required_routes": [{"method": "GET", "path": "/posts"}],
            "required_tables": [{"table": "Post"}],
            "required_fields": [{"table": "Post", "field": "id"}, {"table": "Post", "field": "title"}],
        },
        "assumptions": [],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a blog API.",
        schema_plan=schema_plan,
        api_contract=api_contract,
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    schema_delta = {
        "summary": "Add published flag.",
        "no_schema_change": False,
        "ddl_sql": ["ALTER TABLE posts ADD COLUMN published BOOLEAN;"],
        "schema": {
            "tables": [
                {
                    "name": "posts",
                    "columns": [{"name": "published", "type": "BOOLEAN", "filter": True}],
                }
            ]
        },
        "affected_tables": ["posts"],
        "affected_fields": ["posts.published"],
        "assumptions": [],
        "validation": {"passed": True},
    }
    api_delta = {
        "summary": "Filter posts by published.",
        "requirements": [{"id": "R2", "description": "Filter posts by published."}],
        "routes": [{"method": "GET", "path": "/posts"}],
        "auth": {},
        "websocket_events": [],
        "artifact_contract": {
            "required_routes": [{"method": "GET", "path": "/posts"}],
            "required_tables": [{"table": "Post"}],
            "required_fields": [{"table": "Post", "field": "published"}],
            "required_filters": [{"table": "Post", "field": "published"}],
        },
        "target_file_hints": ["models.py", "schemas.py", "routers/posts.py"],
        "assumptions": [],
        "missing_info": [],
    }
    provider = ScriptedProvider([json.dumps(schema_delta), json.dumps(api_delta)])
    saved_memory = {
        "api_contract": {"contract": api_contract, "artifact_contract": api_contract["artifact_contract"], "version": 1},
        "file_plan": {"plan": {}, "version": 1},
    }

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Add a published boolean field to posts and let list posts filter by it.",
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[api_contract["artifact_contract"]],
        saved_memory=saved_memory,
    )

    assert result.edit_plan["intent"] == "model_spec_rendered_edit"
    assert "published: bool" in (tmp_path / "models.py").read_text(encoding="utf-8")
    assert "Post.published == published" in (tmp_path / "routers" / "posts.py").read_text(encoding="utf-8")
    assert result.stage_outputs["validation"]["accepted"] is True


def test_edit_renderer_uses_prompt_intent_when_api_delta_fails(tmp_path) -> None:
    schema_plan = {
        "domain": "store",
        "ddl_sql": "",
        "schema": {
            "tables": [
                {
                    "name": "orders",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "customer_id", "type": "INTEGER", "references": "customers(id)"},
                        {"name": "status", "type": "TEXT", "filter": True},
                    ],
                },
                {
                    "name": "customers",
                    "columns": [{"name": "id", "type": "INTEGER"}, {"name": "email", "type": "TEXT"}],
                },
            ]
        },
        "validation": {"passed": True},
        "assumptions": [],
    }
    api_contract = {
        "summary": "Store API.",
        "requirements": [{"id": "R1", "description": "Manage orders."}],
        "routes": [{"method": "GET", "path": "/orders"}],
        "auth": {},
        "websocket_events": [],
        "artifact_contract": {
            "required_routes": [{"method": "GET", "path": "/orders"}],
            "required_tables": [{"table": "Order"}, {"table": "Customer"}],
            "required_fields": [{"table": "Order", "field": "customer_id"}],
            "required_filters": [{"table": "Order", "field": "status"}],
        },
        "assumptions": [],
    }
    rendered = render_fastapi_sqlmodel_project(prompt="Build a public store API.", schema_plan=schema_plan, api_contract=api_contract)
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            "not json",
            "still not json",
        ]
    )
    saved_memory = {
        "schema": {"ddl_sql": schema_plan["ddl_sql"], "schema": schema_plan["schema"], "version": 1},
        "api_contract": {"contract": api_contract, "artifact_contract": api_contract["artifact_contract"], "version": 1},
        "file_plan": {"plan": {}, "version": 1},
    }

    result = ModelPatchEditPipeline(provider).edit(
        prompt=(
            "I want an api to get the top 10 costumers that have orders and add price to orders "
            "then make an api to paginate the orders 6 each pagination for each costumer"
        ),
        root=tmp_path,
        index=build_project_index(tmp_path),
        saved_memory=saved_memory,
    )

    assert result.edit_plan["intent"] == "model_spec_rendered_edit"
    assert result.edit_plan["run_report"]["completed_quests"]
    assert result.edit_plan["run_report"]["failed_quests"] == []
    assert result.edit_plan["run_report"]["missing_artifacts"] == []
    assert "models.py" in result.edit_plan["run_report"]["changed_files"]
    canonical_spec = result.stage_outputs["canonical_spec"]
    operation_kinds = {item["kind"] for item in canonical_spec["operations"]}
    assert {"top_related", "parent_children_paginated"} <= operation_kinds
    assert canonical_spec["summary"]["has_custom_behavior"] is True
    models = (tmp_path / "models.py").read_text(encoding="utf-8")
    customers = (tmp_path / "routers" / "customers.py").read_text(encoding="utf-8")
    assert "price: float" in models
    assert '@router.get("/top-by-orders")' in customers
    assert '@router.get("/{customer_id}/orders"' in customers
    assert "page_size: int = Query(default=6" in customers
    assert "Order.customer_id == customer_id" in customers


def test_exact_store_edit_prompt_uses_intent_without_metadata_or_customer_crud(tmp_path) -> None:
    original_prompt = (
        "Plan a FastAPI app for online store; resources are orders. "
        "Resource endpoints can be public. Attach websocket notifications. "
        "Keep the output directive-based, with create, list, detail, update actions and filters for orders: status."
    )
    edit_prompt = (
        "I want an api to get the top 10 costumers that have orders and add price to orders "
        "then make an api to paginate the orders (6  each pagination) for each costumer"
    )
    wrapped_prompt = (
        "Conversation-derived backend change request.\n"
        "Resolved state: recent_user_messages=2\n\n"
        "User messages, oldest to newest:\n"
        f"1. {original_prompt}\n"
        f"2. {edit_prompt}\n"
    )
    schema_plan = {
        "domain": "store",
        "ddl_sql": "",
        "schema": {
            "tables": [
                {
                    "name": "orders",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "status", "type": "TEXT", "filter": True},
                    ],
                }
            ]
        },
        "validation": {"passed": True},
        "assumptions": [],
    }
    api_contract = {
        "summary": "Public order API with websocket notifications.",
        "requirements": [{"id": "R1", "description": "Manage public orders with status filter."}],
        "artifact_contract": {
            "required_routes": [
                {"method": "POST", "path": "/orders"},
                {"method": "GET", "path": "/orders"},
                {"method": "GET", "path": "/orders/{order_id}"},
                {"method": "PUT", "path": "/orders/{order_id}"},
            ],
            "required_tables": [{"table": "Order"}],
            "required_fields": [{"table": "Order", "field": "status"}],
            "required_filters": [{"table": "Order", "field": "status"}],
            "required_behaviors": [{"behavior": "websocket"}, {"behavior": "websocket_notification"}],
        },
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt=original_prompt,
        schema_plan=schema_plan,
        api_contract=api_contract,
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = ScriptedProvider([no_schema_delta_json(), "not json", "still not json"])
    result = ModelPatchEditPipeline(provider).edit(
        prompt=wrapped_prompt,
        root=tmp_path,
        index=build_project_index(tmp_path),
        saved_memory={
            "schema": {"ddl_sql": schema_plan["ddl_sql"], "schema": schema_plan["schema"], "version": 1},
            "api_contract": {
                "contract": api_contract,
                "artifact_contract": api_contract["artifact_contract"],
                "version": 1,
            },
            "file_plan": {"plan": {}, "version": 1},
        },
    )

    assert result.stage_outputs["validation"]["accepted"] is True
    assert provider.calls == []
    assert any(event["stage"] == "edit.api_delta" and event["status"] == "deterministic" for event in result.stage_outputs["trace"])
    models = (tmp_path / "models.py").read_text(encoding="utf-8")
    customers = (tmp_path / "routers" / "customers.py").read_text(encoding="utf-8")
    assert "recent_user_messages" not in models
    assert "class Newest" not in models
    assert "costumer_id" not in models
    assert "price: float" in models
    assert 'customer_id: int | None = Field(default=None, index=True, foreign_key="customers.id")' in models
    assert "class Customer" in models
    assert '@router.get("/top-by-orders")' in customers
    assert '@router.get("/{customer_id}/orders"' in customers
    assert "page_size: int = Query(default=6" in customers
    assert "func.count(Order.id)" in customers
    assert "Order.customer_id == Customer.id" in customers
    assert "Order.customer_id == customer_id" in customers
    assert "@router.post" not in customers
    assert "@router.put" not in customers


def test_patch_edit_repairs_malformed_patch_json(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(),
            "not json",
            json.dumps(
                {
                    "summary": "Repair malformed patch output.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": 'return {"status": "json-repaired"}',
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Change the response text.",
        root=tmp_path,
        index=build_project_index(tmp_path),
    )

    assert result.usage.retries == 1
    assert 'return {"status": "json-repaired"}' in (tmp_path / "main.py").read_text(encoding="utf-8")


def test_patch_edit_falls_back_to_full_file_edit(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(),
            json.dumps({"error": "patch failed"}),
            json.dumps({"error": "patch repair failed"}),
            json.dumps(
                {
                    "summary": "Full file update.",
                    "files": [
                        {
                            "path": "main.py",
                            "content": """
from fastapi import FastAPI

app = FastAPI()


@app.get("/ping")
def endpoint():
    return {"status": "fullfile"}
""".lstrip(),
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Change the response text.",
        root=tmp_path,
        index=build_project_index(tmp_path),
    )

    assert result.edit_plan["intent"] == "model_full_file_edit"
    assert 'return {"status": "fullfile"}' in (tmp_path / "main.py").read_text(encoding="utf-8")


def test_patch_edit_repairs_patch_that_fails_static_validation(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(),
            json.dumps(
                {
                    "summary": "Bad patch with undefined body name.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": "return missing_status",
                        }
                    ],
                    "missing_info": [],
                }
            ),
            json.dumps(
                {
                    "summary": "Repair static failure.",
                    "files": [
                        {
                            "path": "main.py",
                            "content": """
from fastapi import FastAPI

app = FastAPI()


@app.get("/ping")
def endpoint():
    return {"status": "validation-repaired"}
""".lstrip(),
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Change the response text.",
        root=tmp_path,
        index=build_project_index(tmp_path),
    )

    assert result.edit_plan["intent"] == "model_full_file_edit"
    assert result.edit_plan["validation_repaired"] is True
    assert result.edit_plan["validation_repair_attempts"] == 1
    assert any(event["stage"] == "edit.validation_repair_step" for event in result.stage_outputs["trace"])
    assert 'return {"status": "validation-repaired"}' in (tmp_path / "main.py").read_text(encoding="utf-8")


def test_patch_edit_rejects_placeholder_content(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(),
            json.dumps(
                {
                    "summary": "Bad placeholder edit.",
                    "target_files": ["main.py"],
                    "patches": [
                        {"op": "create_file", "path": "notes.txt", "content": "FULL replacement content"}
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    with pytest.raises(ValidationError, match="placeholder marker"):
        ModelPatchEditPipeline(provider).edit(
            prompt="Change the response text.",
            root=tmp_path,
            index=build_project_index(tmp_path),
        )


def test_patch_set_rejects_zero_change_mutation(tmp_path) -> None:
    minimal_project(tmp_path)

    with pytest.raises(ValidationError, match="applied no source changes"):
        apply_patch_set(
            tmp_path,
            {
                "patches": [
                    {
                        "op": "replace_text",
                        "path": "main.py",
                        "old": 'return {"status": "ok"}',
                        "new": 'return {"status": "ok"}',
                    }
                ]
            },
        )


def test_patch_set_supports_ast_import_field_and_router_wiring(tmp_path) -> None:
    write(
        tmp_path / "models.py",
        """
from sqlmodel import SQLModel


class Order(SQLModel, table=True):
    id: int | None = None
""".lstrip(),
    )
    write(
        tmp_path / "main.py",
        """
from fastapi import FastAPI

app = FastAPI()
""".lstrip(),
    )

    changed = apply_patch_set(
        tmp_path,
        {
            "patches": [
                {"op": "insert_import", "path": "models.py", "statement": "from datetime import datetime"},
                {"op": "insert_class_field", "path": "models.py", "class_name": "Order", "content": "created_at: datetime"},
                {
                    "op": "insert_router_wiring",
                    "path": "main.py",
                    "import_statement": "from routers.orders import router as orders_router",
                    "include_statement": "app.include_router(orders_router)",
                },
            ]
        },
    )

    assert [item.path for item in changed] == ["models.py", "models.py", "main.py"]
    models = (tmp_path / "models.py").read_text(encoding="utf-8")
    main = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert "from datetime import datetime" in models.splitlines()[:2]
    assert "    created_at: datetime" in models
    assert "from routers.orders import router as orders_router" in main
    assert "app.include_router(orders_router)" in main


class UnusableProvider:
    """Stands in for a small local model that never returns a usable edit."""

    model = "unusable"

    def __init__(self, scripted: list[str] | None = None) -> None:
        self.scripted = list(scripted or [])
        self.calls = 0

    def complete(self, *args, **kwargs):
        from app.llm.providers import LLMResponse

        self.calls += 1
        content = self.scripted.pop(0) if self.scripted else "I could not edit that."
        return LLMResponse(content=content, model=self.model, input_tokens=10, output_tokens=5)


def _rendered_task_project(tmp_path):
    schema_plan = {
        "domain": "tasks",
        "schema": {"tables": [{"name": "tasks", "columns": [{"name": "id"}, {"name": "title"}]}]},
    }
    api_contract = {
        "requirements": [{"id": "R1", "description": "Expose tasks.", "critical": True}],
        "artifact_contract": {
            "required_tables": [{"table": "tasks"}],
            "required_routes": [{"method": "GET", "path": "/tasks"}],
        },
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a task API.",
        schema_plan=schema_plan,
        api_contract=api_contract,
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)
    return schema_plan, api_contract


def test_literal_edit_on_rendered_project_skips_the_model_code_calls(tmp_path) -> None:
    schema_plan, api_contract = _rendered_task_project(tmp_path)
    provider = UnusableProvider()

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Add a done field to the tasks table.",
        root=tmp_path,
        index=build_project_index(tmp_path),
        saved_memory={"schema": schema_plan, "api_contract": {"contract": api_contract}},
        model_first=True,
    )

    assert provider.calls == 0, "a literal edit on a rendered project needs no code generation call"
    assert result.edit_plan["intent"] == "model_spec_rendered_edit"
    assert "done" in (tmp_path / "models.py").read_text(encoding="utf-8")
    assert any(
        event.get("stage") == "edit.strategy" and event.get("status") == "renderer_first"
        for event in result.stage_outputs["trace"]
    )


def test_edit_falls_back_to_renderer_when_model_patch_and_full_file_fail(tmp_path) -> None:
    schema_plan, api_contract = _rendered_task_project(tmp_path)
    # A prompt with no literal artifacts stays model-first; the planning stages
    # answer, and every code stage after them fails.
    provider = UnusableProvider([no_schema_delta_json(), edit_contract_json("/tasks")])

    result = ModelPatchEditPipeline(provider).edit(
        prompt="Improve how notes are listed.",
        root=tmp_path,
        index=build_project_index(tmp_path),
        saved_memory={"schema": schema_plan, "api_contract": {"contract": api_contract}},
        model_first=True,
    )

    assert provider.calls > 2, "the model path must be tried before the renderer fallback"
    assert result.edit_plan["intent"] == "model_spec_rendered_edit"
    assert result.changed, "the fallback must write the edit instead of rejecting it"
    assert any(
        event.get("stage") == "edit.rendered_fallback" and event.get("status") == "accepted"
        for event in result.stage_outputs["trace"]
    )


def test_rendered_edit_does_not_write_real_project_when_quest_fails(monkeypatch, tmp_path) -> None:
    schema_plan = {
        "domain": "tasks",
        "schema": {"tables": [{"name": "tasks", "columns": [{"name": "id"}, {"name": "priority"}]}]},
    }
    api_contract = {
        "requirements": [{"id": "R1", "description": "Expose tasks.", "critical": True}],
        "artifact_contract": {
            "required_tables": [{"table": "Task"}],
            "required_fields": [{"table": "Task", "field": "priority"}],
            "required_routes": [{"method": "GET", "path": "/tasks"}],
        },
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a task API.",
        schema_plan=schema_plan,
        api_contract=api_contract,
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)
    before = (tmp_path / "main.py").read_text(encoding="utf-8")

    def fail_quests(*args, **kwargs):
        return {
            "completed": [],
            "failed": [{"id": "route:GET:/tasks"}],
            "missing_artifacts": ["R1 missing route GET /tasks"],
            "regressions": [],
            "files_that_would_change": ["main.py"],
            "accepted_paths": [],
            "final_validation": {"accepted": False},
            "context_budget": {},
        }

    monkeypatch.setattr("app.services.model_pipeline.editing.execute_rendered_quests", fail_quests)
    result = ModelPatchEditPipeline(ScriptedProvider([])).try_rendered_edit(
        prompt="Add priority to tasks.",
        root=tmp_path,
        schema_delta={
            "schema": {"tables": [{"name": "tasks", "columns": [{"name": "id"}, {"name": "priority"}, {"name": "done"}]}]},
            "no_schema_change": False,
        },
        edit_contract={
            "requirements": api_contract["requirements"],
            "artifact_contract": {
                "required_fields": [{"table": "Task", "field": "done"}],
            },
            "canonical_spec": {"resources": [{"table": "tasks"}]},
        },
        previous_contracts=None,
        saved_memory={"schema": schema_plan, "api_contract": {"contract": api_contract}},
        usage=ModelRunUsage(),
    )

    assert result is None
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == before


def test_explicit_book_sales_discount_edit_uses_deterministic_renderer_fast_path(tmp_path) -> None:
    schema_plan = {
        "domain": "book_lending",
        "ddl_sql": [
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author_id INTEGER);",
            "CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);",
        ],
        "schema": {
            "tables": [
                {
                    "name": "books",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "title", "type": "TEXT"},
                        {"name": "author_id", "type": "INTEGER", "foreign_key": "authors.id"},
                    ],
                },
                {"name": "authors", "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "TEXT"}]},
            ]
        },
    }
    api_contract = {
        "summary": "Book lending API.",
        "requirements": [{"id": "R1", "description": "Manage books and authors.", "critical": True}],
        "routes": [
            {"method": "GET", "path": "/books"},
            {"method": "POST", "path": "/books"},
            {"method": "GET", "path": "/authors"},
            {"method": "POST", "path": "/authors"},
        ],
        "auth": {},
        "websocket_events": [],
        "artifact_contract": {
            "required_routes": [{"method": "GET", "path": "/books"}, {"method": "GET", "path": "/authors"}],
            "required_tables": [{"table": "books"}, {"table": "authors"}],
            "required_fields": [{"table": "books", "field": "title"}, {"table": "authors", "field": "name"}],
            "required_relationships": [{"from_table": "books", "field": "author_id", "to_table": "authors"}],
        },
        "assumptions": [],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a FastAPI backend for book lending.",
        schema_plan=schema_plan,
        api_contract=api_contract,
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = NonScriptedProvider()
    result = ModelPatchEditPipeline(provider).edit(
        prompt="add an API to return ordered the most sold books, and add discount table for books where I see what offers we have on special things and add them",
        root=tmp_path,
        index=build_project_index(tmp_path),
        saved_memory={
            "schema": {"domain": schema_plan["domain"], "ddl_sql": schema_plan["ddl_sql"], "schema": schema_plan["schema"]},
            "api_contract": {"contract": api_contract, "artifact_contract": api_contract["artifact_contract"]},
            "file_plan": {"plan": {}},
        },
        previous_contracts=[api_contract["artifact_contract"]],
    )

    assert provider.calls == 0
    assert result.edit_plan["intent"] == "model_spec_rendered_edit"
    assert result.stage_outputs["validation"]["accepted"] is True
    trace = result.stage_outputs["trace"]
    assert any(event["stage"] == "edit.schema_delta" and event["status"] == "deterministic" for event in trace)
    assert any(event["stage"] == "edit.api_delta" and event["status"] == "deterministic" for event in trace)
    assert "class BookSale" in (tmp_path / "models.py").read_text(encoding="utf-8")
    models_text = (tmp_path / "models.py").read_text(encoding="utf-8")
    assert "class Discount" in models_text
    assert "class Return" not in models_text
    books_router = (tmp_path / "routers" / "books.py").read_text(encoding="utf-8")
    assert '@router.get("/top-by-sales")' in books_router
    assert '@router.get("/{book_id}/discounts"' in books_router


def test_patch_router_package_path_maps_to_existing_flat_router(tmp_path) -> None:
    write(
        tmp_path / "routers" / "notes.py",
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n",
    )

    changed = apply_patch_set(
        tmp_path,
        {
            "patches": [
                {
                    "op": "append_function",
                    "path": "routers/notes/router.py",
                    "content": "def download_notes():\n    return 'notes'",
                }
            ]
        },
    )

    assert [item.path for item in changed] == ["routers/notes.py"]
    assert "def download_notes" in (tmp_path / "routers" / "notes.py").read_text(encoding="utf-8")


def test_live_like_provider_handles_easy_create_edit_edit_without_model_calls(tmp_path) -> None:
    baseline_contract = {
        "required_routes": [
            {"method": "POST", "path": "/notes"},
            {"method": "GET", "path": "/notes"},
            {"method": "GET", "path": "/notes/{note_id}"},
            {"method": "PUT", "path": "/notes/{note_id}"},
            {"method": "DELETE", "path": "/notes/{note_id}"},
        ],
        "required_tables": [{"table": "notes"}],
        "required_fields": [
            {"table": "notes", "field": field}
            for field in ("title", "body", "pinned")
        ],
        "required_filters": [{"table": "notes", "field": "pinned"}],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build notes CRUD with pinned filtering.",
        schema_plan={
            "schema": {
                "tables": [
                    {
                        "name": "notes",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "title", "type": "TEXT"},
                            {"name": "body", "type": "TEXT"},
                            {"name": "pinned", "type": "BOOLEAN", "filter": True},
                        ],
                    }
                ]
            }
        },
        api_contract={"artifact_contract": baseline_contract},
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = NonScriptedProvider()
    tag_result = ModelPatchEditPipeline(provider).edit(
        prompt="Add a tag field to notes and let list notes filter by tag.",
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )
    download_result = ModelPatchEditPipeline(provider).edit(
        prompt=(
            "Add an endpoint GET /notes/pinned/download that downloads a text file "
            "containing all pinned notes with title and body."
        ),
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract, tag_result.artifact_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )

    assert provider.calls == 0
    assert tag_result.stage_outputs["validation"]["accepted"] is True
    assert download_result.stage_outputs["validation"]["accepted"] is True
    models = (tmp_path / "models.py").read_text(encoding="utf-8")
    notes = (tmp_path / "routers" / "notes.py").read_text(encoding="utf-8")
    assert "tag: str" in models
    assert "Note.tag == tag" in notes
    assert '@router.get("/pinned/download")' in notes
    assert "Note.pinned == True" in notes
    assert "Response(" in notes


def test_live_like_provider_handles_medium_role_and_scoped_download_without_model_calls(tmp_path) -> None:
    baseline_contract = {
        "required_routes": [
            {"method": method, "path": path}
            for method, path in (
                ("POST", "/tasks"),
                ("GET", "/tasks"),
                ("GET", "/tasks/{task_id}"),
                ("PUT", "/tasks/{task_id}"),
                ("DELETE", "/tasks/{task_id}"),
            )
        ],
        "required_tables": [{"table": "users"}, {"table": "tasks"}],
        "required_fields": [
            {"table": "tasks", "field": field}
            for field in ("title", "description", "status", "priority", "assignee_id")
        ],
        "required_filters": [
            {"table": "tasks", "field": field}
            for field in ("status", "priority", "assignee_id")
        ],
        "required_relationships": [
            {"from_table": "tasks", "field": "assignee_id", "to_table": "users"}
        ],
        "required_behaviors": [{"behavior": "jwt_authentication"}],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build users and tasks with JWT auth and task filtering.",
        schema_plan={
            "schema": {
                "tables": [
                    {"name": "users", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {
                        "name": "tasks",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "title", "type": "TEXT"},
                            {"name": "description", "type": "TEXT"},
                            {"name": "status", "type": "TEXT", "filter": True},
                            {"name": "priority", "type": "TEXT", "filter": True},
                            {
                                "name": "assignee_id",
                                "type": "INTEGER",
                                "filter": True,
                                "references": "users(id)",
                            },
                        ],
                    },
                ]
            }
        },
        api_contract={"artifact_contract": baseline_contract},
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = NonScriptedProvider()
    role_result = ModelPatchEditPipeline(provider).edit(
        prompt="Add roles to users and make delete task admin-only.",
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )
    download_result = ModelPatchEditPipeline(provider).edit(
        prompt=(
            "Add an endpoint GET /tasks/unfinished/download that downloads a text file "
            "containing only the current user's unfinished tasks, including title and description."
        ),
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract, role_result.artifact_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )

    assert provider.calls == 0
    assert role_result.stage_outputs["validation"]["accepted"] is True
    assert download_result.stage_outputs["validation"]["accepted"] is True
    tasks = (tmp_path / "routers" / "tasks.py").read_text(encoding="utf-8")
    auth = (tmp_path / "auth.py").read_text(encoding="utf-8")
    assert 'require_role(current_user, "admin")' in tasks
    assert '@router.get("/unfinished/download")' in tasks
    assert "Task.assignee_id == current_user.id" in tasks
    assert 'Task.status.notin_(["completed", "finished"])' in tasks
    assert '@router.post("/token")' in auth


def test_live_like_provider_handles_hard_school_edit_pipeline_without_model_calls(tmp_path) -> None:
    resources = ("students", "teachers", "courses", "enrollments", "assignments")
    baseline_contract = {
        "accepted": True,
        "required_routes": [
            {"method": method, "path": path}
            for resource in resources
            for method, path in (
                ("POST", f"/{resource}"),
                ("GET", f"/{resource}"),
                ("GET", f"/{resource}/{{{resource[:-1]}_id}}"),
                ("PUT", f"/{resource}/{{{resource[:-1]}_id}}"),
                ("DELETE", f"/{resource}/{{{resource[:-1]}_id}}"),
            )
        ],
        "required_tables": [{"table": table} for table in ("users", *resources)],
        "required_fields": [
            {"table": "assignments", "field": field}
            for field in ("title", "description", "status", "course_id", "due_date")
        ],
        "required_filters": [
            {"table": "assignments", "field": field}
            for field in ("course_id", "status")
        ],
        "required_relationships": [
            {"from_table": "courses", "field": "teacher_id", "to_table": "teachers"},
            {"from_table": "enrollments", "field": "student_id", "to_table": "students"},
            {"from_table": "enrollments", "field": "course_id", "to_table": "courses"},
            {"from_table": "assignments", "field": "course_id", "to_table": "courses"},
        ],
        "required_behaviors": [
            {"behavior": "jwt_authentication"},
            {
                "behavior": "role_restricted",
                "role": "teacher",
                "method": "POST",
                "path": "/assignments",
            },
            {
                "behavior": "current_user_scoped",
                "method": "POST",
                "path": "/assignments",
            },
            {
                "behavior": "role_restricted",
                "role": "student",
                "method": "GET",
                "path": "/assignments",
            },
            {
                "behavior": "current_user_scoped",
                "method": "GET",
                "path": "/assignments",
            },
            {
                "behavior": "role_restricted",
                "role": "admin",
                "method": "DELETE",
                "path": "/courses/{course_id}",
            },
        ],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt=(
            "Build a school operations backend with JWT roles. Teachers create assignments "
            "for their courses, students list their assignments, and admins delete courses."
        ),
        schema_plan={
            "schema": {
                "tables": [
                    {"name": "users", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {"name": "students", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {"name": "teachers", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {
                        "name": "courses",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "name", "type": "TEXT"},
                        ],
                    },
                    {
                        "name": "enrollments",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "student_id", "type": "INTEGER", "references": "students(id)"},
                            {"name": "course_id", "type": "INTEGER", "references": "courses(id)"},
                        ],
                    },
                    {
                        "name": "assignments",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "title", "type": "TEXT"},
                            {"name": "description", "type": "TEXT"},
                            {"name": "status", "type": "TEXT", "filter": True},
                            {"name": "course_id", "type": "INTEGER", "filter": True, "references": "courses(id)"},
                            {"name": "due_date", "type": "TIMESTAMP"},
                        ],
                    },
                ]
            }
        },
        api_contract={"artifact_contract": baseline_contract},
    )
    assert rendered is not None
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)

    provider = NonScriptedProvider()
    submission_result = ModelPatchEditPipeline(provider).edit(
        prompt=(
            "Add submissions with assignment_id, student_id, content, grade, and submitted_at. "
            "Include create, list, detail, update, and delete endpoints, and let list submissions "
            "filter by assignment_id and student_id."
        ),
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )
    report_result = ModelPatchEditPipeline(provider).edit(
        prompt=(
            "Add an endpoint GET /students/me/unfinished-assignments/download that downloads a CSV "
            "report of the current student's unfinished assignments with title, course name, due date, "
            "and description."
        ),
        root=tmp_path,
        index=build_project_index(tmp_path),
        previous_contracts=[baseline_contract, submission_result.artifact_contract],
        saved_memory={"file_plan": {"plan": {}}},
    )

    assert provider.calls == 0
    assert submission_result.stage_outputs["validation"]["accepted"] is True
    assert report_result.stage_outputs["validation"]["accepted"] is True
    models = (tmp_path / "models.py").read_text(encoding="utf-8")
    assignments = (tmp_path / "routers" / "assignments.py").read_text(encoding="utf-8")
    courses = (tmp_path / "routers" / "courses.py").read_text(encoding="utf-8")
    submissions = (tmp_path / "routers" / "submissions.py").read_text(encoding="utf-8")
    students = (tmp_path / "routers" / "students.py").read_text(encoding="utf-8")
    assert 'foreign_key="assignments.id"' in models
    assert 'foreign_key="students.id"' in models
    assert "grade: float" in models
    assert "submitted_at: datetime" in models
    assert 'require_role(current_user, "teacher")' in assignments
    assert "Teacher.user_id == current_user.id" in assignments
    assert 'require_role(current_user, "student")' in assignments
    assert "Student.user_id == current_user.id" in assignments
    assert 'require_role(current_user, "admin")' in courses
    assert "Submission.assignment_id == assignment_id" in submissions
    assert "Submission.student_id == student_id" in submissions
    assert '@router.get("/me/unfinished-assignments/download")' in students
    assert "Student.user_id == current_user.id" in students
    assert "csv.writer" in students
    assert "writer.writerow(['title', 'course_name', 'due_date', 'description'])" in students

def test_patch_prompt_uses_external_selected_files_and_rich_index(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Update response.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": 'return {"status": "selected"}',
                        }
                    ],
                    "missing_info": [],
                }
            )
        ]
    )
    index = build_project_index(tmp_path)

    patch_set, selected, _usage = ModelPatchEditPipeline(provider).build_patch_set(
        prompt="Change database.py but only use selected files.",
        root=tmp_path,
        index=index,
        edit_contract={
            "requirements": [{"id": "R1", "description": "Change response text."}],
            "required_routes": [{"requirement_id": "R1", "method": "GET", "path": "/ping"}],
        },
        selected=["main.py"],
    )
    payload = json.loads(provider.calls[0]["messages"][1].content)

    assert selected == ["main.py"]
    assert patch_set["target_files"] == ["main.py"]
    assert payload["selected_files"] == ["main.py"]
    assert payload["project_index"]["selected_files"] == ["main.py"]
    assert payload["project_index"]["existing_routes"] == ["GET /ping -> main.py:endpoint"]
    # The code stage carries a lean index so the source stays intact and anchorable.
    assert "files" not in payload["project_index"]
    assert payload["source_files"][0]["path"] == "main.py"
    assert payload["source_files"][0]["content"] == (tmp_path / "main.py").read_text(encoding="utf-8")

def test_schema_delta_accepts_model_schema_mapping_shape(tmp_path) -> None:
    minimal_project(tmp_path)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "summary": "Add task schema.",
                    "no_schema_change": False,
                    "ddl_sql": "CREATE TABLE Task (id INTEGER PRIMARY KEY, title TEXT);",
                    "schema": {"Task": {"id": "INTEGER PRIMARY KEY", "title": "TEXT"}},
                    "affected_tables": ["Task"],
                    "affected_fields": ["id", "title"],
                    "assumptions": [],
                }
            )
        ]
    )

    schema_delta, usage = ModelPatchEditPipeline(provider).plan_schema_delta(
        prompt="Add task schema.",
        index=build_project_index(tmp_path),
    )

    assert usage.retries == 0
    assert schema_delta["validation"]["passed"] is True
    assert schema_delta["schema"]["tables"][0]["name"] == "Task"
    assert schema_delta["schema"]["tables"][0]["columns"][1]["name"] == "title"


def test_deterministic_edit_handles_field_and_filter_without_custom_route(tmp_path) -> None:
    minimal_project(tmp_path)
    prompt = "Add a tag field to notes and let list notes filter by tag."
    index = build_project_index(tmp_path)
    intent = extract_prompt_intent(
        prompt,
        existing_schema={"schema": {"tables": [{"name": "notes", "columns": [{"name": "id"}]}]}},
    )

    schema_delta = _deterministic_schema_delta_from_intent(prompt, intent, index=index)
    edit_contract = _deterministic_api_delta_from_intent(prompt, intent)

    assert schema_delta is not None
    assert schema_delta["validation"]["passed"] is True
    assert "notes.tag" in schema_delta["affected_fields"]
    assert edit_contract is not None
    contract = edit_contract["artifact_contract"]
    assert any(item.get("table") == "Note" and item.get("field") == "tag" for item in contract["required_fields"])
    assert any(item.get("table") == "Note" and item.get("field") == "tag" for item in contract["required_filters"])
