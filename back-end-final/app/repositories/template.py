from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.template import ProjectTemplate
from app.repositories.base import BaseRepository


class ProjectTemplateRepository(BaseRepository[ProjectTemplate]):
    model = ProjectTemplate

    async def list_active(self) -> list[ProjectTemplate]:
        stmt = (
            select(ProjectTemplate)
            .where(ProjectTemplate.is_active.is_(True))
            .order_by(ProjectTemplate.name)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def detail(self, template_id: UUID) -> ProjectTemplate | None:
        stmt = (
            select(ProjectTemplate)
            .options(selectinload(ProjectTemplate.files))
            .where(ProjectTemplate.id == template_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
