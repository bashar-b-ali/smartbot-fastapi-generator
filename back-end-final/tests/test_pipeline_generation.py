from __future__ import annotations

import json

from pipeline_fixtures import (
    ScriptedProvider,
    api_contract_json,
    database_file,
    file_plan_json,
    main_file,
    requirements_file,
    schema_json,
    wrapped_file,
    write,
)

from app.llm.file_spec import FileSpec
from app.services.model_pipeline.generation import (
    ModelOwnedGenerationPipeline,
    _file_spec_from_model_text,
    _role_instruction,
)
from app.services.model_pipeline.quests import build_generation_quests
from app.services.model_pipeline.renderer import render_fastapi_sqlmodel_project
from app.services.model_pipeline.replay import replay_stage_outputs
from app.services.model_pipeline.validation import candidate_quality_gate, validate_file_specs


def test_generation_builds_model_planned_files() -> None:
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(),
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    assert {item.path for item in result.files} == {"main.py", "database.py", "requirements.txt"}
    assert result.stage_outputs["validation"]["accepted"] is True
    assert [event["stage"] for event in result.stage_outputs["trace"][:5]] == [
        "generation.schema",
        "generation.schema_validation",
        "generation.prompt_intent",
        "generation.api_contract",
        "generation.file_plan",
    ]


def test_direct_file_generation_strips_unclosed_markdown_fence() -> None:
    spec = _file_spec_from_model_text(
        """
```python
from fastapi import FastAPI

app = FastAPI()
""".strip(),
        "main.py",
    )

    assert spec is not None
    assert spec.content.startswith("from fastapi import FastAPI")


def test_generation_accepts_complete_project_candidate_before_per_file() -> None:
    complete_project = "\n".join(
        [
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
            wrapped_file("auth.py", "def current_user():\n    return {'id': 1}\n"),
        ]
    )
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(["main.py", "database.py", "requirements.txt", "auth.py"]),
            complete_project,
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert {item.path for item in result.files} == {"main.py", "database.py", "requirements.txt", "auth.py"}
    assert len(provider.calls) == 4
    assert provider.calls[-1]["max_tokens"] == 12000
    assert any(event["stage"] == "generation.validation.complete_project" for event in result.stage_outputs["trace"])
    assert not any(event["stage"].startswith("generation.file.") for event in result.stage_outputs["trace"])


def test_generation_diagnoses_and_retries_incomplete_complete_project() -> None:
    complete_retry = "\n".join(
        [
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
            wrapped_file("auth.py", "def current_user():\n    return {'id': 1}\n"),
        ]
    )
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(["main.py", "database.py", "requirements.txt", "auth.py"]),
            main_file(route="/ping"),
            json.dumps(
                {
                    "reason": "candidate returned only one wrapper while the plan requires multiple files",
                    "missing_files": ["database.py", "requirements.txt", "auth.py"],
                    "likely_cause": "model stopped after the entrypoint",
                    "retry_strategy": "return every planned file wrapper",
                    "retry_recommended": True,
                }
            ),
            complete_retry,
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert {item.path for item in result.files} == {"main.py", "database.py", "requirements.txt", "auth.py"}
    trace_stages = [event["stage"] for event in result.stage_outputs["trace"]]
    assert "generation.complete_project_diagnosis" in trace_stages
    assert "generation.complete_project_retry" in trace_stages
    assert "generation.validation.complete_project_retry" in trace_stages
    assert not any(event["stage"].startswith("generation.file.") for event in result.stage_outputs["trace"])


def test_generation_falls_back_to_per_file_when_complete_project_is_rejected() -> None:
    rejected_project = "\n".join(
        [
            main_file(route="/wrong"),
            wrapped_file("database.py", "from fastapi import FastAPI\n\napp = FastAPI()\n"),
            requirements_file(),
            wrapped_file("auth.py", "def current_user():\n    return {'id': 1}\n"),
        ]
    )
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(["main.py", "database.py", "requirements.txt", "auth.py"]),
            rejected_project,
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
            wrapped_file("auth.py", "def current_user():\n    return {'id': 1}\n"),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert {item.path for item in result.files} == {"main.py", "database.py", "requirements.txt", "auth.py"}
    complete_events = [
        event for event in result.stage_outputs["trace"] if event["stage"] == "generation.validation.complete_project"
    ]
    assert complete_events[0]["status"] == "rejected"
    assert result.stage_outputs["validation"]["accepted"] is True
    assert any(event["stage"] == "generation.file.main.py" for event in result.stage_outputs["trace"])


def test_file_plan_and_wrappers_canonicalize_src_entry_files() -> None:
    provider = ScriptedProvider(
        [
            file_plan_json(["src/main.py", "src/database.py", "src/requirements.txt", "src/routers/resource.py"]),
            wrapped_file("src/main.py", "from fastapi import FastAPI\napp = FastAPI()\n"),
        ]
    )
    pipeline = ModelOwnedGenerationPipeline(provider)

    plan, _usage = pipeline.plan_files("Build any API.", json.loads(schema_json()), json.loads(api_contract_json()))
    spec, _usage = pipeline.generate_planned_file(
        prompt="Build any API.",
        schema_plan=json.loads(schema_json()),
        api_contract=json.loads(api_contract_json()),
        file_plan=plan,
        file_item={"path": "main.py", "purpose": "entry"},
        previous_summaries=[],
    )

    assert {"main.py", "database.py", "requirements.txt", "src/routers/resource.py"} == {
        item["path"] for item in plan["files"]
    }
    assert spec.path == "main.py"


def test_file_plan_canonicalizes_app_py_entrypoint_alias() -> None:
    provider = ScriptedProvider(
        [
            file_plan_json(["app.py", "database.py", "requirements.txt"]),
        ]
    )
    pipeline = ModelOwnedGenerationPipeline(provider)

    plan, _usage = pipeline.plan_files("Build any API.", json.loads(schema_json()), json.loads(api_contract_json()))

    assert [item["path"] for item in plan["files"]] == ["main.py", "database.py", "requirements.txt"]


def test_file_plan_adds_persistence_model_role_when_schema_has_tables() -> None:
    provider = ScriptedProvider(
        [
            file_plan_json(["main.py", "database.py", "requirements.txt", "routers/resource.py"]),
        ]
    )
    pipeline = ModelOwnedGenerationPipeline(provider)

    plan, _usage = pipeline.plan_files(
        "Build any database API.",
        json.loads(schema_json(tables=[{"name": "resources", "columns": [{"name": "id"}]}])),
        json.loads(api_contract_json(route="/resources", table="Resource")),
    )

    assert "models.py" in {item["path"] for item in plan["files"]}


def test_dependency_file_generation_uses_small_dependency_only_prompt() -> None:
    provider = ScriptedProvider(["fastapi\nuvicorn\nsqlalchemy\npydantic\npython-jose\npasslib\n"])
    pipeline = ModelOwnedGenerationPipeline(provider)

    spec, usage = pipeline.generate_planned_file(
        prompt="Build any API.",
        schema_plan=json.loads(schema_json(tables=[{"name": "items", "columns": [{"name": "id"}]}])),
        api_contract=json.loads(api_contract_json()),
        file_plan=json.loads(file_plan_json()),
        file_item={"path": "requirements.txt", "purpose": "dependencies"},
        previous_summaries=[{"path": "main.py", "chars": 5000}],
    )

    assert spec.path == "requirements.txt"
    assert "fastapi" in spec.content
    assert provider.calls[0]["max_tokens"] == 700
    assert "previous_file_summaries" not in provider.calls[0]["messages"][1].content
    assert usage.retries == 0


def test_dependency_file_generation_repairs_runaway_dependency_output() -> None:
    provider = ScriptedProvider(
        [
            "Here are many dependencies:\nSQLAlchemy-Util-LazyLoader-Async-PostgreSQL\n" * 30,
            "fastapi\nuvicorn\nsqlalchemy\npydantic\n",
        ]
    )
    pipeline = ModelOwnedGenerationPipeline(provider)

    spec, usage = pipeline.generate_planned_file(
        prompt="Build any API.",
        schema_plan=json.loads(schema_json()),
        api_contract=json.loads(api_contract_json()),
        file_plan=json.loads(file_plan_json()),
        file_item={"path": "requirements.txt", "purpose": "dependencies"},
        previous_summaries=[],
    )

    assert spec.content == "fastapi\nuvicorn\nsqlalchemy\npydantic\n"
    assert usage.retries == 1
    assert provider.calls[1]["max_tokens"] == 400


def test_file_generation_repairs_prose_response_into_target_wrapper() -> None:
    provider = ScriptedProvider(
        [
            "The provided file plan describes the main.py entrypoint, but this is not source code.",
            wrapped_file("main.py", "from fastapi import FastAPI\napp = FastAPI()\n"),
        ]
    )
    pipeline = ModelOwnedGenerationPipeline(provider)

    spec, usage = pipeline.generate_planned_file(
        prompt="Build any API.",
        schema_plan=json.loads(schema_json()),
        api_contract=json.loads(api_contract_json()),
        file_plan=json.loads(file_plan_json()),
        file_item={"path": "main.py", "purpose": "entry"},
        previous_summaries=[],
    )

    assert spec.path == "main.py"
    assert "app = FastAPI()" in spec.content
    assert usage.retries == 1
    assert provider.calls[1]["temperature"] == 0.0
    assert any(event["stage"] == "generation.file.main.py.format_repair" for event in pipeline.trace)


def test_file_generation_accepts_clean_direct_single_file_content() -> None:
    provider = ScriptedProvider(["from fastapi import FastAPI\n\napp = FastAPI()\n"])
    pipeline = ModelOwnedGenerationPipeline(provider)

    spec, usage = pipeline.generate_planned_file(
        prompt="Build any API.",
        schema_plan=json.loads(schema_json()),
        api_contract=json.loads(api_contract_json()),
        file_plan=json.loads(file_plan_json()),
        file_item={"path": "main.py", "purpose": "entry"},
        previous_summaries=[],
    )

    assert spec.path == "main.py"
    assert "app = FastAPI()" in spec.content
    assert usage.retries == 0


def test_static_validation_rejects_wrong_file_roles_and_placeholders() -> None:
    files = [
        FileSpec(
            path="main.py",
            content="from fastapi import FastAPI\n\napp = FastAPI()\n",
        ),
        FileSpec(
            path="database.py",
            content="from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/x')\ndef x():\n    pass\n",
        ),
        FileSpec(
            path="auth.py",
            content="from fastapi import FastAPI\n\napp = FastAPI()\n\ndef login():\n    pass\n",
        ),
        FileSpec(path="requirements.txt", content="fastapi\nsqlmodel\n"),
    ]

    result = validate_file_specs(files, {"required_routes": []})

    assert result["static_safe"] is False
    errors = " ".join(check["error"] for check in result["static"]["checks"] if not check["passed"])
    assert "database file contains route handlers" in errors
    assert "non-entrypoint file instantiates FastAPI application" in errors
    assert "contains placeholder function bodies" in errors


def test_static_validation_rejects_undefined_route_signature_names() -> None:
    files = [
        FileSpec(
            path="main.py",
            content="""
from fastapi import Depends, FastAPI

app = FastAPI()


@app.get("/items", response_model=list[Item])
def list_items(user=Depends(get_current_user)):
    return []
""".lstrip(),
        ),
        FileSpec(path="database.py", content="from sqlmodel import Session, create_engine\n\nengine = create_engine('sqlite:///x.db')\n\ndef get_session():\n    yield\n"),
        FileSpec(path="requirements.txt", content="fastapi\nsqlmodel\n"),
    ]

    result = validate_file_specs(files, {"required_routes": []})

    assert result["static_safe"] is False
    errors = " ".join(check["error"] for check in result["static"]["checks"] if not check["passed"])
    assert "references undefined names" in errors
    assert "Item" in errors
    assert "get_current_user" in errors


def test_static_validation_rejects_undefined_route_body_names() -> None:
    files = [
        FileSpec(
            path="main.py",
            content="""
from fastapi import FastAPI

app = FastAPI()


@app.get("/items")
def list_items():
    return missing_items
""".lstrip(),
        ),
        FileSpec(path="database.py", content="from sqlmodel import Session, create_engine\n\nengine = create_engine('sqlite:///x.db')\n\ndef get_session():\n    yield\n"),
        FileSpec(path="requirements.txt", content="fastapi\nsqlmodel\n"),
    ]

    result = validate_file_specs(files, {"required_routes": []})

    assert result["static_safe"] is False
    errors = " ".join(check["error"] for check in result["static"]["checks"] if not check["passed"])
    assert "body references undefined names" in errors
    assert "missing_items" in errors


def test_validation_reports_pydantic_only_model_quality_issue() -> None:
    files = [
        FileSpec(path="main.py", content="from fastapi import FastAPI\napp = FastAPI()\n"),
        FileSpec(path="database.py", content="from sqlmodel import create_engine\nengine = create_engine('sqlite:///x.db')\n"),
        FileSpec(path="models.py", content="from pydantic import BaseModel\n\nclass User(BaseModel):\n    id: int\n"),
        FileSpec(path="requirements.txt", content="fastapi\nsqlmodel\npydantic\n"),
    ]
    contract = {"required_tables": [{"table": "User"}]}

    result = validate_file_specs(files, contract)
    gate = candidate_quality_gate(files, result, contract)

    assert result["failure_category"] == "missing_table"
    assert "pydantic_only_model" in result["failure_reasons"]["categories"]
    assert gate["blocked"] is True
    assert any(issue["category"] == "pydantic_only_model" for issue in gate["issues"])


def test_generation_repairs_static_role_failures_into_accepted_project() -> None:
    repaired_project = "\n".join(
        [
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
        ]
    )
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(),
            main_file(route="/ping"),
            wrapped_file(
                "database.py",
                "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/bad')\ndef bad():\n    pass\n",
            ),
            requirements_file(),
            repaired_project,
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert {item.path for item in result.files} == {"main.py", "database.py", "requirements.txt"}
    assert any(item["mode"] == "focused_regeneration" and item["accepted"] for item in result.stage_outputs["repair"])


def test_generation_runs_only_targeted_repair_after_blocked_focused_repair_fails() -> None:
    focused_bad_project = "\n".join(
        [
            main_file(route="/ping"),
            wrapped_file(
                "database.py",
                "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/bad')\ndef bad():\n    pass\n",
            ),
            requirements_file(),
        ]
    )
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(),
            main_file(route="/ping"),
            wrapped_file(
                "database.py",
                "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/bad')\ndef bad():\n    pass\n",
            ),
            requirements_file(),
            focused_bad_project,
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any compact FastAPI status API.")

    repair_modes = [item["mode"] for item in result.stage_outputs["repair"]]
    trace_stages = [item["stage"] for item in result.stage_outputs["trace"]]
    assert repair_modes == ["focused_regeneration"]
    assert "generation.repair" not in trace_stages
    assert result.stage_outputs["repair"][-1]["accepted"] is False
    assert result.stage_outputs["validation"]["accepted"] is False


def test_api_contract_is_enriched_with_schema_tables_fields_and_filters() -> None:
    provider = ScriptedProvider([api_contract_json(route="/tasks")])
    pipeline = ModelOwnedGenerationPipeline(provider)
    schema_plan = json.loads(
        schema_json(
            tables=[
                {
                    "name": "tasks",
                    "columns": [
                        {"name": "id"},
                        {"name": "priority", "index": True},
                        {"name": "done", "index": True},
                    ],
                    "indexes": [{"columns": ["priority", "done"]}],
                }
            ]
        )
    )

    api_contract, _usage = pipeline.plan_api_contract("Build any task API.", schema_plan)
    artifact_contract = api_contract["artifact_contract"]

    assert {"table": "tasks"} in artifact_contract["required_tables"]
    assert {"table": "tasks", "field": "priority"} in artifact_contract["required_fields"]
    assert {"table": "tasks", "field": "done"} in artifact_contract["required_fields"]
    assert {"table": "tasks", "field": "priority"} in artifact_contract["required_filters"]
    assert {"table": "tasks", "field": "done"} in artifact_contract["required_filters"]


def test_schema_indexes_are_not_promoted_to_required_filters() -> None:
    provider = ScriptedProvider([api_contract_json(route="/events", table="Event")])
    pipeline = ModelOwnedGenerationPipeline(provider)
    schema_plan = json.loads(
        schema_json(
            tables=[
                {
                    "name": "events",
                    "columns": [{"name": "id"}, {"name": "starts_at"}],
                    "indexes": [{"columns": ["starts_at"]}],
                }
            ]
        )
    )

    api_contract, _usage = pipeline.plan_api_contract("Build any event API with no extra filters.", schema_plan)

    assert {"table": "events", "field": "starts_at"} not in api_contract["artifact_contract"]["required_filters"]


def test_root_router_and_model_paths_get_strict_role_instructions() -> None:
    assert "Use APIRouter" in _role_instruction("router.py")
    assert "Do not instantiate FastAPI" in _role_instruction("router.py")
    assert "persistence model file" in _role_instruction("model.py")


def test_generation_repairs_malformed_schema_json() -> None:
    provider = ScriptedProvider(["not json", schema_json()])
    pipeline = ModelOwnedGenerationPipeline(provider)

    plan, usage = pipeline.plan_schema("Build any API.")

    assert plan["validation"]["passed"] is True
    assert usage.retries == 1
    assert any(event["stage"] == "generation.schema_json_repair" for event in pipeline.trace)


def test_generation_keeps_best_static_safe_candidate_after_bad_repairs() -> None:
    provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/missing"),
            file_plan_json(),
            main_file(route="/present", status="initial"),
            database_file(),
            requirements_file(),
            "not file wrappers",
            wrapped_file("main.py", "from fastapi import FastAPI\napp = FastAPI()\n"),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any API.")

    main = next(item for item in result.files if item.path == "main.py")
    assert 'return {"status": "initial"}' in main.content
    assert result.stage_outputs["validation"]["static_safe"] is True
    assert any(event["stage"] == "generation.candidate_selection" for event in result.stage_outputs["trace"])


def test_generation_uses_spec_renderer_for_database_backed_projects() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {
                        "name": "resources",
                        "columns": [
                            {"name": "id"},
                            {"name": "name", "type": "VARCHAR", "index": True},
                        ],
                    }
                ]
            ),
            api_contract_json(route="/resources", table="Resource", field="name"),
            file_plan_json(),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build any resource API with name filters.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert result.plan["intent"] == "model_spec_rendered_generation"
    assert "Resource" in {item["table"] for item in result.artifact_contract["required_tables"]}
    assert "model_spec_sqlmodel_renderer" in json.dumps(result.stage_outputs["render_plan"])
    assert {item.path for item in result.files} >= {
        "main.py",
        "database.py",
        "models.py",
        "schemas.py",
        "routers/resources.py",
        "requirements.txt",
    }
    assert len(provider.calls) == 2
    assert not any(event["stage"] == "generation.file_plan" for event in result.stage_outputs["trace"])


def test_generation_canonical_spec_adds_full_crud_contract_for_resources() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {
                        "name": "resources",
                        "columns": [{"name": "id"}, {"name": "name", "type": "TEXT"}],
                    }
                ]
            ),
            api_contract_json(route="/resources", table="Resource", field="name"),
            file_plan_json(),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build a CRUD FastAPI backend for resources.")

    routes = {
        (route["method"], route["path"])
        for route in result.artifact_contract["required_routes"]
    }
    assert ("DELETE", "/resources/{resource_id}") in routes
    assert result.stage_outputs["canonical_spec"]["summary"]["resource_count"] == 1
    assert "delete_resource" in next(item.content for item in result.files if item.path == "routers/resources.py")


def test_spec_renderer_output_covers_filters_and_modular_roles() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {
                        "name": "orders",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "status", "type": "VARCHAR", "index": True},
                            {"name": "customer_id", "type": "INTEGER", "references": "customers(id)"},
                        ],
                    },
                    {
                        "name": "customers",
                        "columns": [{"name": "id", "type": "INTEGER"}, {"name": "email", "type": "VARCHAR"}],
                    },
                ]
            ),
            api_contract_json(route="/orders", table="Order", field="status"),
            file_plan_json(["main.py", "database.py", "models.py", "schemas.py", "routers/orders.py", "requirements.txt"]),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build("Build an order API. Protect resource endpoints with auth.")

    assert result.stage_outputs["validation"]["accepted"] is True
    assert "auth.py" in {item.path for item in result.files}
    models = next(item.content for item in result.files if item.path == "models.py")
    orders = next(item.content for item in result.files if item.path == "routers/orders.py")
    assert 'foreign_key="customers.id"' in models
    assert "Order.status == status" in orders
    assert "Depends(get_current_user)" in orders


def test_prompt_intent_forces_literal_filter_public_routes_and_notifications() -> None:
    provider = ScriptedProvider(
        [
            schema_json(tables=[{"name": "orders", "columns": [{"name": "id"}]}]),
            api_contract_json(route="/orders", table="Order"),
            file_plan_json(["main.py", "database.py", "models.py", "schemas.py", "routers/orders.py", "requirements.txt"]),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Plan a FastAPI app for online store; resources are orders. "
        "Resource endpoints can be public. Attach websocket notifications. "
        "Keep the output directive-based, with create, list, detail, update actions and filters for orders: status."
    )

    assert result.stage_outputs["validation"]["accepted"] is True
    assert "auth.py" not in {item.path for item in result.files}
    models = next(item.content for item in result.files if item.path == "models.py")
    orders = next(item.content for item in result.files if item.path == "routers/orders.py")
    websockets = next(item.content for item in result.files if item.path == "websockets.py")
    assert "status: str" in models
    assert "status: str | None = Query(default=None)" in orders
    assert "Order.status == status" in orders
    assert "Depends(get_current_user)" not in orders
    assert "delete_order" not in orders
    assert "await broadcast_notification" in orders
    assert "broadcast_notification" in websockets
    routes = {(route["method"], route["path"]) for route in result.artifact_contract["required_routes"]}
    assert ("DELETE", "/orders/{order_id}") not in routes


def test_generation_falls_back_to_prompt_schema_when_model_schema_is_unusable() -> None:
    provider = ScriptedProvider(
        [
            json.dumps({"domain": "store", "ddl_sql": "", "schema": {"tables": []}, "assumptions": []}),
            api_contract_json(route="/orders", table="Order"),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Plan a FastAPI app for online store; resources are orders. "
        "Resource endpoints can be public. Attach websocket notifications. "
        "Keep the output directive-based, with create, list, detail, update actions and filters for orders: status."
    )

    trace_stages = [event["stage"] for event in result.stage_outputs["trace"]]
    models = next(item.content for item in result.files if item.path == "models.py")
    assert result.stage_outputs["validation"]["accepted"] is True
    assert "generation.schema_prompt_fallback" in trace_stages
    assert "generation.schema_repair" not in trace_stages
    assert "status: str" in models


def test_literal_resources_are_drift_guard_for_renderer_fast_path() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {"name": "orders", "columns": [{"name": "id"}]},
                    {"name": "order_items", "columns": [{"name": "id"}, {"name": "order_id"}]},
                ]
            ),
            json.dumps(
                {
                    "project_name": "online_store",
                    "summary": "Online store API.",
                    "requirements": [{"id": "R1", "description": "Expose orders.", "critical": True}],
                    "routes": [
                        {"method": "GET", "path": "/orders"},
                        {"method": "POST", "path": "/orders/items"},
                        {"method": "GET", "path": "/orders/items"},
                    ],
                    "auth": None,
                    "websocket_events": [{"name": "order_changed"}],
                    "artifact_contract": {
                        "required_routes": [
                            {"method": "GET", "path": "/orders"},
                            {"method": "POST", "path": "/orders/items"},
                            {"method": "GET", "path": "/orders/items"},
                        ],
                        "required_tables": [{"table": "Order"}, {"table": "OrderItem"}],
                    },
                    "assumptions": [],
                }
            ),
            file_plan_json(
                [
                    "main.py",
                    "database.py",
                    "models.py",
                    "schemas.py",
                    "routers/orders.py",
                    "routers/order_items.py",
                    "requirements.txt",
                ]
            ),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Plan a FastAPI app for online store; resources are orders. "
        "Resource endpoints can be public. Attach websocket notifications. "
        "Keep the output directive-based, with create, list, detail, update actions and filters for orders: status."
    )

    trace_stages = [event["stage"] for event in result.stage_outputs["trace"]]
    rendered = "\n".join(item.content for item in result.files)
    routes = {(item["method"], item["path"]) for item in result.artifact_contract["required_routes"]}

    assert result.stage_outputs["validation"]["accepted"] is True
    assert result.stage_outputs["generation_strategy"] == "renderer_fast_path"
    assert "order_items" not in {item.path for item in result.files}
    assert "order_items" not in rendered
    assert ("POST", "/orders/items") not in routes
    assert ("GET", "/orders/items") not in routes
    assert "generation.validation.spec_renderer" in trace_stages
    assert "generation.file_plan" not in trace_stages
    assert not any(stage.startswith("generation.file.") for stage in trace_stages)
    assert "generation.complete_project" not in trace_stages
    assert "generation.repair_focused" not in trace_stages
    assert result.stage_outputs["drift_rejections"]


def test_existing_context_cannot_add_resource_outside_literal_allowlist() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {"name": "orders", "columns": [{"name": "id"}]},
                    {"name": "order_items", "columns": [{"name": "id"}, {"name": "order_id"}]},
                ]
            ),
            json.dumps(
                {
                    "project_name": "online_store",
                    "summary": "Online store API.",
                    "requirements": [{"id": "R1", "description": "Expose orders.", "critical": True}],
                    "routes": [{"method": "GET", "path": "/orders/items"}],
                    "auth": None,
                    "websocket_events": [],
                    "artifact_contract": {
                        "required_routes": [{"method": "GET", "path": "/orders/items"}],
                        "required_tables": [{"table": "Order"}, {"table": "OrderItem"}],
                    },
                    "assumptions": [],
                }
            ),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Plan a FastAPI app for online store; resources are orders. "
        "Keep the output directive-based, with create, list, detail, update actions and filters for orders: status.",
        existing_context="Existing project mentions order_items and /orders/items from an old draft.",
    )

    rendered = "\n".join(item.content for item in result.files)
    routes = {(item["method"], item["path"]) for item in result.artifact_contract["required_routes"]}

    assert result.stage_outputs["validation"]["accepted"] is True
    assert result.stage_outputs["generation_strategy"] == "renderer_fast_path"
    assert "order_items" not in rendered
    assert ("GET", "/orders/items") not in routes
    assert {item["table"] for item in result.artifact_contract["required_tables"]} == {"orders"}


def test_replay_stage_outputs_revalidates_complete_wrappers() -> None:
    stage_outputs = {
        "trace": [
            {
                "stage": "generation.complete_project",
                "raw_response": "\n".join([main_file(route="/ping"), database_file(), requirements_file()]),
            }
        ]
    }

    result = replay_stage_outputs(stage_outputs, {"required_routes": [{"method": "GET", "path": "/ping"}]})

    assert result["replayable"] is True
    assert result["candidate_count"] == 1
    assert result["accepted"] is True


def test_replay_stage_outputs_reports_unreplayable_excerpts() -> None:
    result = replay_stage_outputs({"trace": [{"stage": "generation.file.main.py", "raw_response_excerpt": "no wrappers"}]}, {})

    assert result["replayable"] is False
    assert result["candidate_count"] == 0


def test_micro_quest_plan_for_single_field_is_atomic() -> None:
    quests = build_generation_quests(
        {"resources": [{"table": "Task"}]},
        {
            "required_tables": [{"table": "Task"}],
            "required_fields": [{"table": "Task", "field": "priority"}],
        },
    )

    field_quests = [quest for quest in quests if quest.kind == "field"]
    assert [quest.id for quest in field_quests] == ["field:task.priority"]
    assert field_quests[0].target_files == ["models.py"]
    assert not any(quest.kind == "route" for quest in quests)


def test_micro_quest_plan_for_single_route_is_route_and_wiring_only() -> None:
    quests = build_generation_quests(
        {"resources": [{"table": "tasks"}]},
        {"required_routes": [{"method": "GET", "path": "/tasks"}]},
    )

    assert [quest.kind for quest in quests] == ["schema", "route", "router", "wiring"]
    assert [quest.id for quest in quests if quest.kind == "route"] == ["route:GET:/tasks"]
    assert [quest.target_files for quest in quests if quest.kind == "wiring"] == [["main.py"]]


def test_renderer_generation_reports_quests_for_prompt_artifacts() -> None:
    provider = ScriptedProvider(
        [
            schema_json(tables=[{"name": "tasks", "columns": [{"name": "id"}, {"name": "priority", "index": True}]}]),
            api_contract_json(route="/tasks", table="Task", field="priority"),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Build a task API with tasks and filter tasks by priority."
    )

    quest_ids = {quest["id"] for quest in result.stage_outputs["quests"]}
    assert result.stage_outputs["validation"]["accepted"] is True
    assert {"table:task", "field:task.priority", "route:GET:/tasks", "wiring:tasks"} <= quest_ids
    assert result.stage_outputs["quest_results"]["failed"] == []


def test_quest_context_budget_is_smaller_than_whole_project_context() -> None:
    quests = build_generation_quests(
        {"resources": [{"table": "Task"}]},
        {
            "required_tables": [{"table": "Task"}],
            "required_fields": [{"table": "Task", "field": "priority"}],
            "required_routes": [{"method": "GET", "path": "/tasks"}],
        },
    )

    selected = sum(quest.max_tokens * 4 for quest in quests)
    whole_project = selected + 10_000
    assert selected < whole_project


def test_multi_resource_crud_uses_deterministic_contract_and_keeps_relationship_target() -> None:
    provider = ScriptedProvider(
        [
            schema_json(
                tables=[
                    {
                        "name": "books",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "title", "type": "VARCHAR"},
                            {"name": "author_id", "type": "INTEGER"},
                        ],
                        "relationships": [
                            {"table_name": "books", "column_name": "author_id", "related_table_name": "authors", "related_column_name": "id"}
                        ],
                    },
                    {"name": "authors", "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR"}]},
                ]
            ),
        ]
    )

    result = ModelOwnedGenerationPipeline(provider).build(
        "Build a FastAPI backend for a book lending. It should manage books, and authors. "
        "Resource endpoints can be public. Attach file upload/download helpers. "
        "Include create, list, detail, update, delete endpoints and let list endpoints filter by authors: name."
    )

    assert result.stage_outputs["validation"]["accepted"] is True
    assert len(provider.calls) == 1
    assert any(event["stage"] == "generation.api_contract" and event["status"] == "deterministic" for event in result.stage_outputs["trace"])
    relationships = result.artifact_contract["required_relationships"]
    assert {"from_table": "books", "field": "author_id", "to_table": "authors"} in relationships
    assert {"table": "authors", "field": "name"} in result.artifact_contract["required_filters"]
    models = next(item.content for item in result.files if item.path == "models.py")
    authors_router = next(item.content for item in result.files if item.path == "routers/authors.py")
    assert 'foreign_key="authors.id"' in models
    assert "name: str | None = Query(default=None)" in authors_router
    assert "Author.name == name" in authors_router


def test_renderer_accepts_common_relationship_table_and_column_shape() -> None:
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build courses and teachers.",
        schema_plan={
            "schema": {
                "tables": [
                    {"name": "teachers", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {
                        "name": "courses",
                        "columns": [{"name": "id", "type": "INTEGER"}, {"name": "teacher_id", "type": "INTEGER"}],
                        "relationships": [{"table": "teachers", "column": "teacher_id"}],
                    },
                ]
            }
        },
        api_contract={"artifact_contract": {"required_tables": [{"table": "teachers"}, {"table": "courses"}]}},
    )

    assert rendered is not None
    models = next(item.content for item in rendered.files if item.path == "models.py")
    assert 'foreign_key="teachers.id"' in models


def test_renderer_generates_working_token_flow_and_admin_guard() -> None:
    contract = {
        "required_tables": [{"table": "users"}, {"table": "tasks"}],
        "required_fields": [{"table": "users", "field": "role"}],
        "required_behaviors": [
            {"behavior": "jwt_authentication"},
            {
                "behavior": "role_restricted",
                "table": "tasks",
                "method": "DELETE",
                "path": "/tasks/{task_id}",
                "role": "admin",
            },
        ],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build users and tasks with JWT auth. Delete task is admin only.",
        schema_plan={
            "schema": {
                "tables": [
                    {"name": "users", "columns": [{"name": "id", "type": "INTEGER"}, {"name": "role", "type": "TEXT"}]},
                    {"name": "tasks", "columns": [{"name": "id", "type": "INTEGER"}, {"name": "title", "type": "TEXT"}]},
                ]
            }
        },
        api_contract={"artifact_contract": contract},
    )

    assert rendered is not None
    auth = next(item.content for item in rendered.files if item.path == "auth.py")
    main = next(item.content for item in rendered.files if item.path == "main.py")
    schemas = next(item.content for item in rendered.files if item.path == "schemas.py")
    tasks = next(item.content for item in rendered.files if item.path == "routers/tasks.py")
    assert '@router.post("/token")' in auth
    assert "verify_password(credentials.password" in auth
    assert "session.get(User, user_id)" in auth
    assert "app.include_router(auth_router)" in main
    assert "password: str" in schemas
    assert "password_hash" not in schemas
    assert 'require_role(current_user, "admin")' in tasks
    assert validate_file_specs(rendered.files, contract)["static_safe"] is True


def test_renderer_generates_scoped_unfinished_text_download() -> None:
    contract = {
        "custom_routes": [{"kind": "explicit", "method": "GET", "path": "/tasks/unfinished/download"}],
        "required_behaviors": [
            {"behavior": "file_download", "format": "text", "method": "GET", "path": "/tasks/unfinished/download"},
            {"behavior": "current_user_scoped", "method": "GET", "path": "/tasks/unfinished/download"},
        ],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Download the current user's unfinished tasks as text.",
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
                            {"name": "status", "type": "TEXT"},
                            {"name": "assignee_id", "type": "INTEGER"},
                        ],
                    },
                ]
            }
        },
        api_contract={"artifact_contract": contract},
    )

    assert rendered is not None
    tasks = next(item.content for item in rendered.files if item.path == "routers/tasks.py")
    assert '@router.get("/unfinished/download")' in tasks
    assert 'Task.status.notin_(["completed", "finished"])' in tasks
    assert "Task.assignee_id == current_user.id" in tasks
    assert "Response(" in tasks
    assert "Title: " in tasks
    assert validate_file_specs(rendered.files, contract)["static_safe"] is True


def test_renderer_generates_enrollment_scoped_csv_download() -> None:
    contract = {
        "custom_routes": [
            {
                "kind": "explicit",
                "method": "GET",
                "path": "/students/me/unfinished-assignments/download",
            }
        ],
        "required_tables": [{"table": "assignments"}],
        "required_fields": [
            {"table": "assignments", "field": field}
            for field in ("title", "course_name", "due_date", "description")
        ],
        "required_behaviors": [
            {
                "behavior": "file_download",
                "format": "csv",
                "method": "GET",
                "path": "/students/me/unfinished-assignments/download",
            },
            {
                "behavior": "current_user_scoped",
                "method": "GET",
                "path": "/students/me/unfinished-assignments/download",
            },
        ],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Download the current student's unfinished assignments as CSV.",
        schema_plan={
            "schema": {
                "tables": [
                    {"name": "users", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {"name": "students", "columns": [{"name": "id", "type": "INTEGER"}]},
                    {
                        "name": "enrollments",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "student_id", "type": "INTEGER"},
                            {"name": "course_id", "type": "INTEGER"},
                        ],
                    },
                    {
                        "name": "assignments",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "course_id", "type": "INTEGER"},
                            {"name": "title", "type": "TEXT"},
                            {"name": "course_name", "type": "TEXT"},
                            {"name": "due_date", "type": "TIMESTAMP"},
                            {"name": "description", "type": "TEXT"},
                            {"name": "status", "type": "TEXT"},
                        ],
                    },
                ]
            }
        },
        api_contract={"artifact_contract": contract},
    )

    assert rendered is not None
    students = next(item.content for item in rendered.files if item.path == "routers/students.py")
    assert '@router.get("/me/unfinished-assignments/download")' in students
    assert "from models import Student, Assignment, Enrollment" in students
    assert "Enrollment.course_id == Assignment.course_id" in students
    assert "Enrollment.student_id == current_user.id" in students
    assert "csv.writer" in students
    assert validate_file_specs(rendered.files, contract)["static_safe"] is True


def test_renderer_imports_temporal_types_used_by_list_filters() -> None:
    contract = {
        "required_tables": [{"table": "assignments"}],
        "required_routes": [{"method": "GET", "path": "/assignments"}],
        "required_fields": [{"table": "assignments", "field": "due_date"}],
        "required_filters": [{"table": "assignments", "field": "due_date"}],
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="List assignments filtered by due date.",
        schema_plan={
            "schema": {
                "tables": [
                    {
                        "name": "assignments",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "due_date", "type": "TIMESTAMP", "filter": True},
                        ],
                    }
                ]
            }
        },
        api_contract={"artifact_contract": contract},
    )

    assert rendered is not None
    assignments = next(item.content for item in rendered.files if item.path == "routers/assignments.py")
    assert "from datetime import datetime" in assignments
    assert "due_date: datetime | None" in assignments
    assert validate_file_specs(rendered.files, contract)["static_safe"] is True


def test_renderer_serves_the_api_prefix_the_contract_declares(tmp_path) -> None:
    from app.services.project_edit.artifact_validation import validate_artifacts
    from app.services.project_indexer import build_project_index

    contract = {
        "requirements": [{"id": "R1", "description": "Expose books and members.", "critical": True}],
        "required_tables": [{"table": "books"}],
        "required_routes": [
            {"method": "GET", "path": "/api/books"},
            {"method": "GET", "path": "/api/books/{book_id}"},
            {"method": "GET", "path": "/api/members"},
        ],
    }
    schema_plan = {
        "schema": {
            "tables": [
                {"name": "books", "columns": [{"name": "id"}, {"name": "title"}]},
                {"name": "members", "columns": [{"name": "id"}, {"name": "name"}]},
            ]
        }
    }
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a library API.",
        schema_plan=schema_plan,
        api_contract={"artifact_contract": contract},
    )

    assert rendered is not None
    assert rendered.render_plan["api_prefix"] == "/api"
    for spec in rendered.files:
        write(tmp_path / spec.path, spec.content)
    index = build_project_index(tmp_path)
    assert "/api/books" in {route["path"] for route in index["api_routes"]}
    # Declared and served routes must agree, or every route reads as missing.
    assert validate_artifacts(index, contract).as_dict()["passed"] is True


def test_renderer_keeps_plain_routes_when_declared_prefixes_disagree() -> None:
    schema_plan = {"schema": {"tables": [{"name": "books", "columns": [{"name": "id"}]}]}}
    rendered = render_fastapi_sqlmodel_project(
        prompt="Build a library API.",
        schema_plan=schema_plan,
        api_contract={
            "artifact_contract": {
                "required_routes": [
                    {"method": "GET", "path": "/api/books"},
                    {"method": "POST", "path": "/books"},
                ]
            }
        },
    )

    assert rendered is not None
    assert rendered.render_plan["api_prefix"] == ""
    books = next(item.content for item in rendered.files if item.path == "routers/books.py")
    assert 'APIRouter(prefix="/books"' in books
