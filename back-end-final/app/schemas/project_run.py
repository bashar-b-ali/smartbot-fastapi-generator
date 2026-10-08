from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "project-run.v1"

RunOperation = Literal["create", "edit"]
RunStatus = Literal[
    "queued",
    "running",
    "waiting_for_model",
    "needs_input",
    "needs_attention",
    "completed",
    "completed_with_warnings",
    "cancelled",
    "failed",
]


class ProjectRunCreate(BaseModel):
    operation: RunOperation
    prompt: str = Field(min_length=1, max_length=20_000)
    model_id: UUID | None = None
    provider: str = Field(default="auto", max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=120)
    base_revision: str | None = Field(default=None, max_length=64)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Prompt must not be empty")
        return value


class ProjectRunControl(BaseModel):
    instruction: str = Field(default="", max_length=10_000)
    model_id: UUID | None = None


class ProjectRunCheckpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    title: str
    status: str
    stage: str
    attempts: int
    plan: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)
    changed_files: list[dict[str, Any]] = Field(default_factory=list)
    base_revision: str = ""
    applied_revision: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectRunOut(BaseModel):
    contract_version: str = CONTRACT_VERSION
    run_id: UUID
    project_id: UUID
    operation: str
    status: str
    stage: str
    prompt: str
    provider: str
    model_id: UUID | None = None
    current_checkpoint: int = 0
    last_event_sequence: int = 0
    cancellation_requested: bool = False
    plan: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    checkpoints: list[ProjectRunCheckpointOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    status_url: str
    events_url: str


class ProjectRunEventOut(BaseModel):
    contract_version: str = CONTRACT_VERSION
    sequence: int
    type: str
    project_id: UUID
    run_id: UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProjectRunEventsOut(BaseModel):
    contract_version: str = CONTRACT_VERSION
    run_id: UUID
    after: int
    last_sequence: int
    events: list[ProjectRunEventOut]
