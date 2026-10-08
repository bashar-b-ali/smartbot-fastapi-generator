from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models.chat import ChatMessage, ChatSession, ProjectAnalysis, ProjectHelper
from app.repositories.base import BaseRepository


class ChatSessionRepository(BaseRepository[ChatSession]):
    model = ChatSession

    async def list_for_project(self, project_id: UUID) -> list[ChatSession]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.project_id == project_id, ChatSession.is_active.is_(True))
            .order_by(ChatSession.updated_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def with_messages(self, session_id: UUID) -> ChatSession | None:
        stmt = (
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(ChatSession.id == session_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()


class ChatMessageRepository(BaseRepository[ChatMessage]):
    model = ChatMessage

    async def for_session(self, session_id: UUID, *, limit: int = 50) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return list(reversed(rows))


    async def recent_for_project(self, project_id: UUID, *, limit: int = 50) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.project_id == project_id, ChatSession.is_active.is_(True))
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return list(reversed(rows))

    async def mark_interrupted_pending_generation_messages(self) -> int:
        result = await self.db.execute(
            update(ChatMessage)
            .where(ChatMessage.message_type == "assistant", ChatMessage.model_used == "pipeline-running")
            .values(
                model_used="pipeline-interrupted",
                content=(
                    "The previous local model run was interrupted by a server restart. "
                    "Send the request again to start a new run."
                ),
            )
        )
        await self.db.flush()
        return int(result.rowcount or 0)


class ProjectAnalysisRepository(BaseRepository[ProjectAnalysis]):
    model = ProjectAnalysis

    async def for_project(self, project_id: UUID) -> ProjectAnalysis | None:
        stmt = select(ProjectAnalysis).where(ProjectAnalysis.project_id == project_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()


class ProjectHelperRepository(BaseRepository[ProjectHelper]):
    model = ProjectHelper

    async def for_project(self, project_id: UUID) -> ProjectHelper | None:
        stmt = select(ProjectHelper).where(ProjectHelper.project_id == project_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self, project_id: UUID) -> ProjectHelper:
        helper = await self.for_project(project_id)
        if helper:
            return helper
        helper = ProjectHelper(project_id=project_id)
        self.db.add(helper)
        await self.db.flush()
        await self.db.refresh(helper)
        return helper
