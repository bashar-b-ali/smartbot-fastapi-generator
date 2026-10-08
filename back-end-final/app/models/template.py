from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import UUIDChar, utcnow


class ProjectTemplate(Base):
    __tablename__ = "project_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    base_structure: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    required_apps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    default_settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sample_models: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sample_views: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sample_urls: Mapped[str] = mapped_column(Text, default="", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )

    files: Mapped[list[TemplateFile]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class TemplateFile(Base):
    __tablename__ = "template_files"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("project_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    template: Mapped[ProjectTemplate] = relationship(back_populates="files")
