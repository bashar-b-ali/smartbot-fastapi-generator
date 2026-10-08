from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["openai", "anthropic", "gemini", "google", "ollama", "local", "custom"]


class LLMModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None = None
    name: str
    provider: Provider
    model_id: str
    api_base_url: str
    default_max_tokens: int
    temperature: float
    input_cost_per_million: float
    output_cost_per_million: float
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class LLMModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Provider
    model_id: str = Field(min_length=1, max_length=100)
    api_key: str = Field(default="", max_length=2000)
    api_base_url: str = Field(default="", max_length=500)
    default_max_tokens: int = 2048
    temperature: float = 0.7
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    is_active: bool = True
    is_default: bool = False


class UserCustomLLMModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Provider = "custom"
    model_id: str = Field(min_length=1, max_length=100)
    api_key: str = Field(default="", max_length=2000)
    api_base_url: str = Field(default="", max_length=500)
    default_max_tokens: int = 2048
    temperature: float = 0.1
    is_default: bool = False


class UserCustomLLMModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: Provider | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=2000)
    api_base_url: str | None = Field(default=None, max_length=500)
    default_max_tokens: int | None = None
    temperature: float | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class LLMModelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    provider: Provider | None = None
    model_id: str | None = Field(default=None, max_length=100)
    api_key: str | None = None
    api_base_url: str | None = None
    default_max_tokens: int | None = None
    temperature: float | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    is_active: bool | None = None
    is_default: bool | None = None
