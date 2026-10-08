from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDChar, utcnow


class ProjectBlueprint(Base):
    __tablename__ = "project_blueprints"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="generation", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ProjectSchemaVersion(Base):
    __tablename__ = "project_schema_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("project_blueprints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="generation", nullable=False)
    ddl_sql: Mapped[str] = mapped_column(Text, default="", nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ProjectApiContractVersion(Base):
    __tablename__ = "project_api_contract_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("project_schema_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="generation", nullable=False)
    contract_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_contract: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ProjectFilePlan(Base):
    __tablename__ = "project_file_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("project_api_contract_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="generation", nullable=False)
    file_plan: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ProjectFileSummary(Base):
    __tablename__ = "project_file_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    file_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class ProjectPipelineRun(Base):
    __tablename__ = "project_pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="generation", nullable=False)
    provider: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="started", nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    stage_outputs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ProjectPipelineTraceEvent(Base):
    __tablename__ = "project_pipeline_trace_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUIDChar(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("project_pipeline_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="", nullable=False, index=True)
    event: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
