from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
from app.models.project import Project
from app.models.user import User
from app.repositories.project_run import ProjectRunRepository
from app.services.project_runs import run_out

pytest.importorskip("aiosqlite")


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _project(db, tmp_path):
    user = User(
        email="runs@example.com",
        username="runs",
        password="hashed",
        is_active=True,
        email_verified=True,
    )
    project = Project(
        user=user,
        name="Durable Runs",
        description="Durable run tests",
        folder_path=str(tmp_path),
        is_active=True,
    )
    db.add_all([user, project])
    await db.flush()
    return user, project


@pytest.mark.asyncio
async def test_run_creation_is_idempotent_and_emits_a_durable_event(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _project(db, tmp_path)
            repo = ProjectRunRepository(db)
            first = await repo.create(
                project_id=project.id,
                user_id=user.id,
                operation="edit",
                prompt="Add a health route",
                provider="ollama",
                model_id=None,
                idempotency_key="request-1",
                request_json={"operation": "edit"},
            )
            second = await repo.create(
                project_id=project.id,
                user_id=user.id,
                operation="edit",
                prompt="This duplicate must not create another run",
                provider="ollama",
                model_id=None,
                idempotency_key="request-1",
                request_json={"operation": "edit"},
            )

            assert second.id == first.id
            events = await repo.events(first.id)
            assert [event.event_type for event in events] == ["project_run.queued"]

            output = await run_out(repo, first)
            assert output.contract_version == "project-run.v1"
            assert output.status == "queued"
            assert output.last_event_sequence == 1
            assert output.status_url.endswith(f"/runs/{first.id}")


@pytest.mark.asyncio
async def test_run_claims_use_leases_and_respect_model_retry_schedule(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _project(db, tmp_path)
            repo = ProjectRunRepository(db)
            run = await repo.create(
                project_id=project.id,
                user_id=user.id,
                operation="create",
                prompt="Create a small API",
                provider="ollama",
                model_id=None,
                idempotency_key=None,
                request_json={"operation": "create"},
            )
            claimed = await repo.claim_next("worker-a", lease_for=timedelta(seconds=45))
            assert claimed is not None
            assert claimed.id == run.id
            assert claimed.status == "running"
            assert claimed.lease_owner == "worker-a"

            checkpoint = await repo.ensure_checkpoint(
                run,
                title="Create requested project changes",
                plan={"sequence": 1},
            )
            checkpoint.status = "retrying"
            run.status = "waiting_for_model"
            run.next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
            await repo.release_lease(run)
            await db.flush()

            assert await repo.claim_next("worker-b", lease_for=timedelta(seconds=45)) is None
            assert (await repo.checkpoints(run.id))[0].status == "retrying"


@pytest.mark.asyncio
async def test_run_repository_persists_ordered_file_checkpoints(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _project(db, tmp_path)
            repo = ProjectRunRepository(db)
            run = await repo.create(
                project_id=project.id,
                user_id=user.id,
                operation="edit",
                prompt="Add a route",
                provider="ollama",
                model_id=None,
                idempotency_key=None,
                request_json={"operation": "edit"},
            )

            first = await repo.ensure_checkpoint(
                run,
                sequence=1,
                title="Edit models.py",
                plan={"path": "models.py"},
            )
            second = await repo.ensure_checkpoint(
                run,
                sequence=2,
                title="Edit routers/items.py",
                plan={"path": "routers/items.py"},
            )

            assert first.sequence == 1
            assert second.sequence == 2
            assert run.current_checkpoint == 2
            assert [item.sequence for item in await repo.checkpoints(run.id)] == [1, 2]
