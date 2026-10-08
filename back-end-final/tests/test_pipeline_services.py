from __future__ import annotations

import json

import pytest
from pipeline_fixtures import (
    ScriptedProvider,
    api_contract_json,
    database_file,
    edit_contract_json,
    file_plan_json,
    main_file,
    minimal_project,
    no_schema_delta_json,
    requirements_file,
    schema_json,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1 import chatbot
from app.db.base import Base
from app.llm.providers import LLMResponse, OllamaProvider
from app.models.pipeline_memory import ProjectPipelineRun, ProjectPipelineTraceEvent
from app.models.project import Project
from app.models.user import User
from app.services.generator import GeneratorService
from app.services.project_editor import ProjectEditService

pytest.importorskip("aiosqlite")


class FakeOllamaClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def chat(self, **request):
        self.requests.append(request)
        return {
            "message": {"content": "ok"},
            "prompt_eval_count": 1,
            "eval_count": 1,
        }


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _db_project(db, tmp_path):
    user = User(
        email="pipeline@example.com",
        username="pipeline",
        password="hashed",
        is_active=True,
        email_verified=True,
    )
    project = Project(
        user=user,
        name="Pipeline Smoke",
        description="Pipeline smoke test",
        folder_path=str(tmp_path),
        is_active=True,
    )
    db.add_all([user, project])
    await db.flush()
    await db.refresh(user)
    await db.refresh(project)
    return user, project


def test_ollama_provider_sends_configured_context_window() -> None:
    provider = OllamaProvider(model="fastAPI_Model", num_ctx=32768)
    fake_client = FakeOllamaClient()
    provider._client = fake_client

    response = provider.complete([], max_tokens=128)

    assert isinstance(response, LLMResponse)
    assert fake_client.requests[0]["options"]["num_ctx"] == 32768
    assert fake_client.requests[0]["options"]["num_predict"] == 128


async def test_generator_service_writes_model_owned_temp_project(monkeypatch, tmp_path) -> None:
    scripted_provider = ScriptedProvider(
        [
            schema_json(),
            api_contract_json(route="/ping"),
            file_plan_json(),
            main_file(route="/ping"),
            database_file(),
            requirements_file(),
        ]
    )

    async def provider_for_generation(self, user, *, provider, model, model_id):  # noqa: ARG001
        return scripted_provider

    monkeypatch.setattr(GeneratorService, "_provider_for_generation", provider_for_generation)

    async for session_factory in _session_factory():
        async with session_factory() as db:
            user, project = await _db_project(db, tmp_path)
            result = await GeneratorService(db).generate_from_prompt(
                project.id,
                user,
                prompt="Build any compact FastAPI status API.",
                provider="mock",
            )

            assert result["accepted"] is True
            assert {item["path"] for item in result["all_files"]} >= {
                "main.py",
                "database.py",
                "requirements.txt",
            }
            assert (tmp_path / "main.py").exists()


async def test_generator_service_writes_spec_rendered_database_generation(monkeypatch, tmp_path) -> None:
    scripted_provider = ScriptedProvider(
        [
            schema_json(tables=[{"name": "resources", "columns": [{"name": "id"}]}]),
            api_contract_json(route="/resources", table="Resource"),
            file_plan_json(),
        ]
        )

    async def provider_for_generation(self, user, *, provider, model, model_id):  # noqa: ARG001
        return scripted_provider

    monkeypatch.setattr(GeneratorService, "_provider_for_generation", provider_for_generation)

    async for session_factory in _session_factory():
        async with session_factory() as db:
            user, project = await _db_project(db, tmp_path)
            result = await GeneratorService(db).generate_from_prompt(
                project.id,
                user,
                prompt="Build any compact resource API.",
                provider="mock",
            )

            assert result["accepted"] is True
            assert result["pipeline_state"] == "accepted"
            assert {item["path"] for item in result["files"]} >= {
                "main.py",
                "database.py",
                "models.py",
                "schemas.py",
                "routers/resources.py",
                "requirements.txt",
            }
            assert (tmp_path / "models.py").exists()
            assert "SQLModel" in (tmp_path / "models.py").read_text(encoding="utf-8")


async def test_project_edit_service_applies_model_patch_to_db_backed_project(
    monkeypatch,
    tmp_path,
) -> None:
    minimal_project(tmp_path)
    scripted_provider = ScriptedProvider(
        [
            no_schema_delta_json(),
            edit_contract_json(route="/ping"),
            json.dumps(
                {
                    "summary": "Update response.",
                    "target_files": ["main.py"],
                    "patches": [
                        {
                            "op": "replace_text",
                            "path": "main.py",
                            "old": 'return {"status": "ok"}',
                            "new": 'return {"status": "service-edit"}',
                        }
                    ],
                    "missing_info": [],
                }
            ),
        ]
    )

    def provider_for_edit(self, model_config, provider_type=None, model=None):  # noqa: ARG001
        return scripted_provider

    monkeypatch.setattr(ProjectEditService, "_provider_for_edit", provider_for_edit)

    async for session_factory in _session_factory():
        async with session_factory() as db:
            user, project = await _db_project(db, tmp_path)
            result = await ProjectEditService(db).edit_project(
                project.id,
                user,
                prompt="Change the status response.",
            )

            assert result["accepted"] is True
            assert [item["path"] for item in result["files"] if item["path"] == "main.py"] == ["main.py"]
            assert 'return {"status": "service-edit"}' in (tmp_path / "main.py").read_text(
                encoding="utf-8"
            )


async def test_pipeline_run_inspection_redacts_and_summarizes_db_backed_trace(tmp_path) -> None:
    async for session_factory in _session_factory():
        async with session_factory() as db:
            user, project = await _db_project(db, tmp_path)
            run = ProjectPipelineRun(
                project_id=project.id,
                source="generation",
                provider="scripted/test",
                status="rejected",
                prompt="Build anything with password=secret-value",
                stats={"input_tokens": 1, "api_key": "secret-token"},
                stage_outputs={
                    "trace": [
                        {
                            "stage": "generation.schema",
                            "raw_response_excerpt": "password=secret-value",
                            "payload": {"large": ["item"] * 20},
                        }
                    ]
                },
                accepted=False,
            )
            db.add(run)
            await db.flush()
            event = ProjectPipelineTraceEvent(
                project_id=project.id,
                run_id=run.id,
                stage="generation.schema",
                status="ok",
                event={"raw_response_excerpt": "api_key=secret-token"},
            )
            db.add(event)
            await db.flush()

            rows = await chatbot.list_pipeline_runs(project.id, user, db, limit=20, offset=0)
            detail = await chatbot.get_pipeline_run(project.id, run.id, user, db)

            assert rows[0].id == run.id
            assert rows[0].stage_summary["trace"]["sample"][0]["raw_response_excerpt"]["excerpt"].endswith(
                "<redacted>"
            )
            assert detail.trace_events[0].event["raw_response_excerpt"]["excerpt"].endswith(
                "<redacted>"
            )


def test_legacy_generation_routes_are_hidden_from_openapi_schema() -> None:
    hidden_paths = {
        "/chatbot/projects/{project_id}/generate-code",
        "/chatbot/projects/{project_id}/generate-app",
    }
    hidden_routes = {
        route.path
        for route in chatbot.router.routes
        if getattr(route, "include_in_schema", True) is False
    }

    assert hidden_paths <= hidden_routes


async def test_legacy_generation_routes_are_disabled_without_old_llm_pipeline(
    monkeypatch,
    tmp_path,
) -> None:
    async def fail_rate_limit(user_id):  # noqa: ARG001
        return None

    async for session_factory in _session_factory():
        async with session_factory() as db:
            user, project = await _db_project(db, tmp_path)

            monkeypatch.setattr(chatbot, "enforce_llm_rate_limit", fail_rate_limit)
            code_result = await chatbot.generate_code(project.id, user, db)
            app_result = await chatbot.generate_app(project.id, user, db)

            assert code_result["pipeline_state"] == "legacy_generation_disabled"
            assert app_result["pipeline_state"] == "legacy_generation_disabled"
            assert "generate-from-prompt" in code_result["replacement"]
            assert "generate-from-prompt" in app_result["replacement"]


def test_async_chat_job_routes_are_registered() -> None:
    paths = {getattr(route, "path", "") for route in chatbot.router.routes}

    assert "/chatbot/projects/{project_id}/chat/start" in paths
    assert "/chatbot/projects/{project_id}/chat/jobs/{job_id}" in paths

@pytest.mark.asyncio
async def test_legacy_chat_route_returns_pending_for_mutation_without_blocking(monkeypatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    calls = {"started": 0, "background": 0}
    session_id = uuid4()

    class FakeChatService:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        def _is_file_mutation_request(self, message: str) -> bool:
            return "add an api" in message.lower()

        async def start_generation_message(self, project_id, user, message, session_id_arg, *, attachments):  # noqa: ANN001
            calls["started"] += 1
            created = datetime.now(UTC)
            session = SimpleNamespace(id=session_id)
            user_msg = SimpleNamespace(
                id=uuid4(),
                session_id=session_id,
                message_type="user",
                content=message,
                tokens_used=0,
                model_used="",
                attachments=attachments,
                created_at=created,
            )
            assistant_msg = SimpleNamespace(
                id=uuid4(),
                session_id=session_id,
                message_type="assistant",
                content="Creating the project with the selected local model.",
                tokens_used=0,
                model_used="pipeline-running",
                attachments=[],
                created_at=created,
            )
            return session, user_msg, assistant_msg, {"context_tokens_saved": 0}

    async def fake_rate_limit(user_id):  # noqa: ANN001
        return None

    class FakeTask:
        def __init__(self, coro):  # noqa: ANN001
            self.coro = coro
            coro.close()

    def fake_create_task(coro):  # noqa: ANN001
        calls["background"] += 1
        return FakeTask(coro)

    monkeypatch.setattr(chatbot, "ChatService", FakeChatService)
    monkeypatch.setattr(chatbot, "enforce_llm_rate_limit", fake_rate_limit)
    monkeypatch.setattr(chatbot.asyncio, "create_task", fake_create_task)

    response = await chatbot.chat(
        uuid4(),
        chatbot.ChatRequest(message="add an API to return ordered the most sold books"),
        SimpleNamespace(id=uuid4()),
        object(),
    )

    assert calls == {"started": 1, "background": 1}
    assert response.generated is False
    assert response.assistant_message.model_used == "pipeline-running"
    assert response.user_message.content.startswith("add an API")


def test_project_stats_exposes_edit_creation_cluster_metrics_schema() -> None:
    from app.schemas.project import ProjectStats

    stats = ProjectStats(
        total_files=1,
        total_folders=1,
        total_size_bytes=10,
        total_size_mb=0.0,
        edit_creation_clusters_metrics={"creation": {"runs": 1}, "edit": {"runs": 2}},
    )

    assert stats.edit_creation_clusters_metrics["creation"]["runs"] == 1
    assert stats.edit_creation_clusters_metrics["edit"]["runs"] == 2

@pytest.mark.asyncio
async def test_chat_service_start_generation_message_uses_real_session_logic(tmp_path) -> None:
    from app.repositories.chat import ChatMessageRepository
    from app.services.chat import ChatService

    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _db_project(db, tmp_path)
            service = ChatService(db)

            session, user_msg, assistant_msg, context_savings = await service.start_generation_message(
                project.id,
                user,
                "add an API to return ordered the most sold books",
                None,
                attachments=[],
            )

            assert session.id == user_msg.session_id == assistant_msg.session_id
            assert user_msg.message_type == "user"
            assert assistant_msg.message_type == "assistant"
            assert assistant_msg.model_used == "pipeline-running"
            assert assistant_msg.created_at > user_msg.created_at
            assert "context_tokens_saved" in context_savings
            rows = await ChatMessageRepository(db).for_session(session.id)
            assert [row.message_type for row in rows] == ["user", "assistant"]

            existing_session, existing_user_msg, existing_assistant_msg, _ = await service.start_generation_message(
                project.id,
                user,
                "add an API to return ordered the most sold books",
                None,
                attachments=[],
            )

            assert existing_session.id == session.id
            assert existing_user_msg.id == user_msg.id
            assert existing_assistant_msg.id == assistant_msg.id
            assert len(await ChatMessageRepository(db).recent_for_project(project.id)) == 2
