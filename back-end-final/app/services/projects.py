from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import logger
from app.models.project import Project, ProjectFile
from app.models.user import User
from app.repositories.project import ProjectFileRepository, ProjectRepository
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services import project_files as fs
from app.services.project_indexer import ProjectIndexer


class ProjectService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProjectRepository(db)
        self.file_repo = ProjectFileRepository(db)

    async def list_for_user(self, user: User) -> list[Project]:
        return await self.repo.list_for_user(user.id)

    async def get_for_user(self, project_id: UUID, user: User) -> Project:
        project = await self.repo.get_for_user(project_id, user.id)
        if not project:
            raise NotFoundError("Project not found")
        return project

    async def create(self, payload: ProjectCreate, user: User) -> Project:
        active = await self.repo.count_active_for_user(user.id)
        if active >= settings.max_projects_per_user:
            raise ConflictError(
                f"Maximum project limit ({settings.max_projects_per_user}) reached"
            )

        project = Project(
            user_id=user.id,
            name=payload.name,
            description=payload.description,
        )
        await self.repo.add(project)

        try:
            project.folder_path = await fs.create_project_folder(user, project)
            await self.db.flush()
        except Exception:
            logger.exception("project.create.folder_failed", project_id=str(project.id))
            await self.db.delete(project)
            raise

        return project

    async def update(self, project_id: UUID, payload: ProjectUpdate, user: User) -> Project:
        project = await self.get_for_user(project_id, user)
        if payload.name is not None:
            project.name = payload.name
        if payload.description is not None:
            project.description = payload.description
        await self.db.flush()
        return project

    async def delete(self, project_id: UUID, user: User) -> None:
        project = await self.get_for_user(project_id, user)
        # Soft-delete in DB; remove folder from disk synchronously so storage is freed.
        # Cascading rows (chat sessions, analyses, helpers) go away via FK ON DELETE CASCADE.
        project.is_active = False
        await self.db.flush()
        await fs.delete_project_folder(project)

    async def record_uploaded_file(
        self, project: Project, rel_dir: str, filename: str, size: int, content_type: str
    ) -> ProjectFile:
        rel = f"{rel_dir}/{filename}".lstrip("/") if rel_dir else filename
        pf = ProjectFile(
            project_id=project.id,
            file_name=filename,
            file_path=rel,
            file_size=size,
            file_type=content_type or "",
        )
        created = await self.file_repo.add(pf)
        await ProjectIndexer(self.db).refresh(project, reason="upload")
        return created
