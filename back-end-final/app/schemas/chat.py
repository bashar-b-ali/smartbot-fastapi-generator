from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Attachment(BaseModel):
    """Reference to a file uploaded as part of a chat message."""
    kind: Literal["image", "file"] = "image"
    name: str
    url: str
    mime: str = ""
    size: int = 0


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    message_type: str
    content: str
    tokens_used: int
    model_used: str
    attachments: list[Attachment] = Field(default_factory=list)
    created_at: datetime


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    title: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ChatSessionWithMessages(ChatSessionOut):
    messages: list[ChatMessageOut] = Field(default_factory=list)


class CreateSession(BaseModel):
    title: str = Field(default="New Chat", max_length=255)


class ChatRequest(BaseModel):
    # Empty string allowed when attachments are present (image-only messages).
    message: str = Field(default="")
    session_id: UUID | None = None
    model_id: UUID | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=255)
    attachments: list[Attachment] = Field(default_factory=list)

    @field_validator("session_id", "model_id", mode="before")
    @classmethod
    def blank_or_default_id_to_none(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "null",
            "none",
            "undefined",
            "default",
            "server-default",
        }:
            return None
        return value

    def model_post_init(self, _ctx) -> None:  # type: ignore[override]
        if not self.message.strip() and not self.attachments:
            raise ValueError("Either `message` or at least one attachment is required.")


class ChatResponse(BaseModel):
    session_id: UUID
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    generated: bool = False
    generated_files: list[dict[str, Any]] = Field(default_factory=list)
    view_file_path: str | None = None
    context_savings: dict[str, Any] = Field(default_factory=dict)
    generation_stats: dict[str, Any] = Field(default_factory=dict)
    edit_plan: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    artifact_validation: dict[str, Any] = Field(default_factory=dict)
    accepted: bool = False
    pipeline_state: str = "unknown"
    failure_category: str = ""
    missing_artifacts: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    pipeline_trace: list[dict[str, Any]] = Field(default_factory=list)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    provider: str = ""
    run_id: UUID | None = None
    run_status: str = ""


class ReviewCodeRequest(BaseModel):
    code: str = Field(min_length=1)
    context: str | None = None
    model_id: UUID | None = None


class FixErrorRequest(BaseModel):
    error: str
    code: str
    traceback: str | None = None
    context: str | None = None
    model_id: UUID | None = None


class ExplainCodeRequest(BaseModel):
    code: str = Field(min_length=1)
    model_id: UUID | None = None


class AnalyzeRequest(BaseModel):
    description: str = Field(min_length=1)
    model_id: UUID | None = None


class ProjectAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    requirements_summary: str
    technical_specifications: str
    database_schema: str
    api_endpoints: list[Any]
    documentation: str
    created_at: datetime
    updated_at: datetime
