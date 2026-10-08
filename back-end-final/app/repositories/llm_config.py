from uuid import UUID

from sqlalchemy import or_, select, update

from app.models.llm_config import LLMModelConfig
from app.repositories.base import BaseRepository


class LLMModelConfigRepository(BaseRepository[LLMModelConfig]):
    model = LLMModelConfig

    async def get_default(self, user_id: UUID | None = None) -> LLMModelConfig | None:
        if user_id:
            user_default = await self.get_user_default(user_id)
            if user_default:
                return user_default

        stmt = select(LLMModelConfig).where(
            LLMModelConfig.user_id.is_(None),
            LLMModelConfig.is_default.is_(True),
            LLMModelConfig.is_active.is_(True),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_default(self, user_id: UUID) -> LLMModelConfig | None:
        stmt = select(LLMModelConfig).where(
            LLMModelConfig.user_id == user_id,
            LLMModelConfig.is_default.is_(True),
            LLMModelConfig.is_active.is_(True),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_active(self, model_id: UUID) -> LLMModelConfig | None:
        stmt = select(LLMModelConfig).where(
            LLMModelConfig.id == model_id,
            LLMModelConfig.is_active.is_(True),
            LLMModelConfig.user_id.is_(None),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_owned(
        self, model_id: UUID, user_id: UUID
    ) -> LLMModelConfig | None:
        stmt = select(LLMModelConfig).where(
            LLMModelConfig.id == model_id,
            LLMModelConfig.user_id == user_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_active_for_user(
        self, model_id: UUID, user_id: UUID
    ) -> LLMModelConfig | None:
        stmt = select(LLMModelConfig).where(
            LLMModelConfig.id == model_id,
            LLMModelConfig.is_active.is_(True),
            or_(LLMModelConfig.user_id == user_id, LLMModelConfig.user_id.is_(None)),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_active(self, user_id: UUID | None = None) -> list[LLMModelConfig]:
        stmt = (
            select(LLMModelConfig)
            .where(LLMModelConfig.is_active.is_(True))
            .order_by(LLMModelConfig.is_default.desc(), LLMModelConfig.name)
        )
        if user_id:
            stmt = stmt.where(
                or_(LLMModelConfig.user_id == user_id, LLMModelConfig.user_id.is_(None))
            )
        else:
            stmt = stmt.where(LLMModelConfig.user_id.is_(None))
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_user_owned(self, user_id: UUID) -> list[LLMModelConfig]:
        stmt = (
            select(LLMModelConfig)
            .where(LLMModelConfig.user_id == user_id)
            .order_by(LLMModelConfig.is_default.desc(), LLMModelConfig.name)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_all(self) -> list[LLMModelConfig]:
        stmt = select(LLMModelConfig).order_by(
            LLMModelConfig.is_default.desc(), LLMModelConfig.name
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def clear_default_except(self, keep_id: UUID) -> None:
        stmt = (
            update(LLMModelConfig)
            .where(
                LLMModelConfig.id != keep_id,
                LLMModelConfig.user_id.is_(None),
                LLMModelConfig.is_default.is_(True),
            )
            .values(is_default=False)
        )
        await self.db.execute(stmt)

    async def clear_user_default_except(self, user_id: UUID, keep_id: UUID | None = None) -> None:
        stmt = update(LLMModelConfig).where(
            LLMModelConfig.user_id == user_id,
            LLMModelConfig.is_default.is_(True),
        )
        if keep_id:
            stmt = stmt.where(LLMModelConfig.id != keep_id)
        await self.db.execute(stmt.values(is_default=False))
