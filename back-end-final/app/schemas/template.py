from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TemplateFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_path: str
    file_name: str
    content: str
    is_required: bool


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    template_type: str
    description: str
    base_structure: dict[str, Any]
    required_apps: list[Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TemplateDetail(TemplateOut):
    sample_models: str
    sample_views: str
    sample_urls: str
    files: list[TemplateFileOut] = Field(default_factory=list)


class CreateProjectFromTemplate(BaseModel):
    template_id: UUID
    project_name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="")
