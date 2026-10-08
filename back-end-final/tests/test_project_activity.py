from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1 import projects as project_routes
from app.db.base import Base
from app.llm.file_spec import FileSpec
from app.llm.writer import write_project
from app.models.project import Project
from app.models.user import User
from app.services.project_activity import (
    clear_orphaned_project_activity,
    clear_project_activity,
    project_activity,
    require_project_not_busy,
    start_project_activity,
)
from app.services.projects import ProjectService

pytest.importorskip("aiosqlite")


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
        email="busy@example.com",
        username="busy",
        password="hashed",
        is_active=True,
        email_verified=True,
    )
    project = Project(
        user=user,
        name="Busy Project",
        description="Busy project test",
        folder_path=str(tmp_path / "projects" / "busy"),
        is_active=True,
    )
    db.add_all([user, project])
    await db.flush()
    await db.refresh(user)
    await db.refresh(project)
    return user, project


@pytest.mark.asyncio
async def test_project_activity_is_status_only_for_in_memory_jobs() -> None:
    project_id = uuid4()

    await start_project_activity(project_id, operation="project_generation", job_id="job-1")
    try:
        activity = await project_activity(project_id)
        assert activity["operation"] == "project_generation"
        await require_project_not_busy(project_id, action="delete_project")
    finally:
        await clear_project_activity(project_id, job_id="job-1")

    assert await project_activity(project_id) == {}


@pytest.mark.asyncio
async def test_project_activity_survives_restart_as_status_only(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            _, project = await _db_project(db, tmp_path)
            await start_project_activity(
                project.id,
                operation="project_generation",
                job_id="job-db",
                message="Generating files",
                db=db,
            )
            await db.commit()
            await clear_project_activity(project.id, job_id="job-db")

        async with factory() as db:
            activity = await project_activity(project.id, db)
            assert activity["durable"] is True
            assert activity["job_id"] == "job-db"
            await require_project_not_busy(project.id, action="delete_project", db=db)

            await clear_project_activity(project.id, db=db)
            await db.commit()
            assert await project_activity(project.id, db) == {}


@pytest.mark.asyncio
async def test_project_delete_continues_while_activity_is_running(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _db_project(db, tmp_path)
            await start_project_activity(project.id, operation="project_generation", job_id="job-delete", db=db)
            await db.commit()

            await ProjectService(db).delete(project.id, user)

            assert project.is_active is False
            assert not (tmp_path / "projects" / "busy").exists()
            await clear_project_activity(project.id, job_id="job-delete", db=db)
            await db.commit()


@pytest.mark.asyncio
async def test_file_delete_route_continues_while_activity_is_running(tmp_path) -> None:
    root = tmp_path / "projects" / "busy"
    root.mkdir(parents=True)
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
    async for factory in _session_factory():
        async with factory() as db:
            user, project = await _db_project(db, tmp_path)
            await start_project_activity(project.id, operation="project_generation", job_id="job-file", db=db)
            await db.commit()

            await project_routes.delete_file(project.id, user, db, path="main.py")

            assert not (root / "main.py").exists()
            await clear_project_activity(project.id, job_id="job-file", db=db)
            await db.commit()


@pytest.mark.asyncio
async def test_late_model_write_does_not_recreate_deleted_project_root(tmp_path) -> None:
    root = tmp_path / "deleted-project"
    root.mkdir()
    root.rmdir()

    with pytest.raises(FileNotFoundError):
        await write_project(root, [FileSpec(path="main.py", content="print('late')\n")])

    assert not root.exists()
@pytest.mark.asyncio
async def test_startup_cleanup_clears_orphaned_activity_runs(tmp_path) -> None:
    async for factory in _session_factory():
        async with factory() as db:
            _, project = await _db_project(db, tmp_path)
            await start_project_activity(project.id, operation="project_generation", job_id="job-reload", db=db)
            await db.commit()

            assert await project_activity(project.id, db)
            cleared = await clear_orphaned_project_activity(db)
            await db.commit()

            assert cleared >= 1
            assert await project_activity(project.id, db) == {}
