from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_path: str
    file_size: int
    file_type: str
    created_at: datetime


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    project_code: str
    description: str
    folder_path: str
    created_at: datetime
    updated_at: datetime
    llm_busy: bool = False
    llm_activity: dict[str, Any] = Field(default_factory=dict)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class FolderEntry(BaseModel):
    name: str
    is_directory: bool
    size: int
    modified: float


class ProjectStats(BaseModel):
    total_files: int
    total_folders: int
    total_size_bytes: int
    total_size_mb: float
    edit_creation_clusters_metrics: dict[str, Any] = Field(default_factory=dict)


class FileContent(BaseModel):
    file_path: str
    content: str
    size: int


class ProjectIndexOut(BaseModel):
    project_id: UUID
    project_context: str = ""
    database_schema: dict[str, Any] = Field(default_factory=dict)
    api_routes: list[dict[str, Any]] = Field(default_factory=list)
    file_index: list[dict[str, Any]] = Field(default_factory=list)
    function_summaries: list[dict[str, Any]] = Field(default_factory=list)
    class_summaries: list[dict[str, Any]] = Field(default_factory=list)
    query_filters: list[dict[str, Any]] = Field(default_factory=list)
    router_wiring: list[dict[str, Any]] = Field(default_factory=list)
    index_meta: dict[str, Any] = Field(default_factory=dict)
    requirement_contracts: list[dict[str, Any]] = Field(default_factory=list)
    recent_changes: list[dict[str, Any]] = Field(default_factory=list)
