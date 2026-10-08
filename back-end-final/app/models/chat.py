from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import UUIDChar, utcnow

if TYPE_CHECKING:
    from app.models.project import Project


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New Chat", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(lazy="joined")
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_type: Mapped[str] = mapped_column(String(10), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_used: Mapped[str] = mapped_column(String(50), default="", nullable=False)

    # List of attachment descriptors: [{kind, name, url, mime, size}, ...]
    # Nullable so existing rows from before the migration are still queryable.
    attachments: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ProjectAnalysis(Base):
    __tablename__ = "project_analysis"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    requirements_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    technical_specifications: Mapped[str] = mapped_column(Text, default="", nullable=False)
    database_schema: Mapped[str] = mapped_column(Text, default="", nullable=False)
    api_endpoints: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    documentation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class ProjectHelper(Base):
    __tablename__ = "project_helpers"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    project_context: Mapped[str] = mapped_column(Text, default="", nullable=False)
    database_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    api_routes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    file_index: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    function_summaries: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    requirement_contracts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recent_changes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )
