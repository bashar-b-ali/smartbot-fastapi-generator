from uuid import UUID

from sqlalchemy import delete, func, select

from app.models.project import Project, ProjectFile
from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    model = Project

    async def get_for_user(self, project_id: UUID, user_id: UUID) -> Project | None:
        stmt = select(Project).where(
            Project.id == project_id,
            Project.user_id == user_id,
            Project.is_active.is_(True),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.user_id == user_id, Project.is_active.is_(True))
            .order_by(Project.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_active_for_user(self, user_id: UUID) -> int:
        stmt = select(func.count(Project.id)).where(
            Project.user_id == user_id, Project.is_active.is_(True)
        )
        return (await self.db.execute(stmt)).scalar_one()


class ProjectFileRepository(BaseRepository[ProjectFile]):
    model = ProjectFile

    async def list_generated_for_project(self, project_id: UUID) -> list[ProjectFile]:
        stmt = (
            select(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.file_type == "generated",
            )
            .order_by(ProjectFile.file_path.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def delete_generated_for_project(self, project_id: UUID) -> None:
        stmt = delete(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.file_type == "generated",
        )
        await self.db.execute(stmt)
        await self.db.flush()

    async def delete_paths_for_project(self, project_id: UUID, paths: list[str]) -> None:
        if not paths:
            return
        stmt = delete(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.file_path.in_(paths),
        )
        await self.db.execute(stmt)
        await self.db.flush()
