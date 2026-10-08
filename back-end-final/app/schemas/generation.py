"""Schemas for model-owned project generation and edit responses."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

Provider = Literal["anthropic", "openai", "gemini", "google", "ollama", "mock", "auto"]


class GenerateFromPromptRequest(BaseModel):
    """Body for POST /projects/{project_id}/generate-from-prompt."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    provider: Provider = "auto"
    model: str | None = Field(default=None, max_length=255)
    model_id: UUID | None = None
    clean: bool = True


class PreviewPlanRequest(BaseModel):
    """Body for POST /projects/{project_id}/preview-plan.

    This asks the model for the requirement contract and file tree without writing files.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    provider: Provider = "auto"
    model: str | None = Field(default=None, max_length=255)
    model_id: UUID | None = None


class PlanPreviewStats(BaseModel):
    input_tokens: int
    output_tokens: int
    retries: int
    file_count: int
    full_context_estimated_tokens: int = 0
    selected_context_estimated_tokens: int = 0
    context_tokens_saved: int = 0
    context_tokens_saved_pct: float = 0.0


class PreviewPlanResponse(BaseModel):
    plan: dict[str, Any]
    file_paths: list[str]
    stats: PlanPreviewStats
    provider: str
    selected_template: dict[str, Any]
    validation: dict[str, Any] = Field(default_factory=dict)
    spec_summary: dict[str, Any] = Field(default_factory=dict)
    clarification_required: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class FileWriteOut(BaseModel):
    path: str
    bytes_written: int


class GenerationStats(BaseModel):
    total_files: int
    total_bytes: int
    input_tokens: int
    output_tokens: int
    retries: int
    changed_files: int = 0
    full_context_estimated_tokens: int = 0
    selected_context_estimated_tokens: int = 0
    context_tokens_saved: int = 0
    context_tokens_saved_pct: float = 0.0
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)


class FileValidationOut(BaseModel):
    path: str
    passed: bool
    error: str = ""


class ProjectValidationOut(BaseModel):
    passed: bool
    accepted: bool = False
    static_safe: bool = False
    write_allowed: bool = False
    pipeline_state: str = "unknown"
    failure_category: str = ""
    checks: list[FileValidationOut]
    missing_required: list[str]
    errors: list[str] = Field(default_factory=list)
    prompt_contract: dict[str, Any] = Field(default_factory=dict)
    requirement_coverage: dict[str, Any] = Field(default_factory=dict)
    artifact_validation: dict[str, Any] = Field(default_factory=dict)
    missing_artifacts: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    model_generation: dict[str, Any] = Field(default_factory=dict)
    model_edit: dict[str, Any] = Field(default_factory=dict)
    ai_refinement: dict[str, Any] = Field(default_factory=dict)
    spec_summary: dict[str, Any] = Field(default_factory=dict)
    draft_reason: str = ""
    runtime_validation: dict[str, Any] = Field(default_factory=dict)
    generation_strategy: str = ""
    drift_rejections: list[dict[str, Any]] = Field(default_factory=list)
    repair_plan: dict[str, Any] = Field(default_factory=dict)
    next_action: str = ""


class GenerateFromPromptResponse(BaseModel):
    project_id: str
    project_root: str
    plan: dict[str, Any]
    files: list[FileWriteOut]
    all_files: list[FileWriteOut] = Field(default_factory=list)
    stats: GenerationStats
    provider: str
    selected_template: dict[str, Any]
    validation: ProjectValidationOut
    artifact_validation: dict[str, Any] = Field(default_factory=dict)
    accepted: bool = False
    pipeline_state: str = "unknown"
    failure_category: str = ""
    missing_artifacts: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    pipeline_trace: list[dict[str, Any]] = Field(default_factory=list)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    context_selection: dict[str, Any] = Field(default_factory=dict)
    spec_summary: dict[str, Any] = Field(default_factory=dict)
    clarification_required: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    draft_reason: str = ""
    runtime_validation: dict[str, Any] = Field(default_factory=dict)
    generation_strategy: str = ""
    drift_rejections: list[dict[str, Any]] = Field(default_factory=list)
    repair_plan: dict[str, Any] = Field(default_factory=dict)
    next_action: str = ""
    working_summary: list[str] = Field(default_factory=list)
    next_prompt: str = ""
    view_file_path: str = ""


class PipelineTraceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    run_id: UUID | None = None
    stage: str
    status: str
    event: dict[str, Any]
    created_at: Any


class PipelineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    project_id: UUID
    source: str
    provider: str
    status: str
    prompt: str
    stats: dict[str, Any]
    stage_summary: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("stage_summary", "stage_outputs"),
    )
    accepted: bool
    created_at: Any


class PipelineRunDetailOut(PipelineRunOut):
    trace_events: list[PipelineTraceEventOut] = Field(default_factory=list)
    replay: dict[str, Any] = Field(default_factory=dict)


class PipelineAuditOut(BaseModel):
    project_id: UUID
    latest_run: PipelineRunOut | None = None
    trace_stages: list[dict[str, Any]] = Field(default_factory=list)
    missing_artifacts: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    pipeline_state: str = "unknown"
    accepted: bool = False
