from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import ValidationError
from app.llm.file_spec import FileSpec
from app.llm.writer import WriteOutcome


@dataclass
class ModelRunUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0

    def add_response(self, response: Any) -> None:
        self.input_tokens += int(getattr(response, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(response, "output_tokens", 0) or 0)

    def add_usage(self, other: ModelRunUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.retries += other.retries


@dataclass
class ModelBuildResult:
    plan: dict[str, Any]
    files: list[FileSpec]
    requirements: list[dict[str, Any]]
    artifact_contract: dict[str, Any]
    provider: str
    usage: ModelRunUsage
    stage_outputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelEditResult:
    changed: list[WriteOutcome]
    requirements: list[dict[str, Any]]
    artifact_contract: dict[str, Any]
    edit_plan: dict[str, Any]
    provider: str
    usage: ModelRunUsage
    selected_files: list[str]
    stage_outputs: dict[str, Any] = field(default_factory=dict)


class ModelPatchValidationError(ValidationError):
    def __init__(
        self,
        message: str,
        *,
        patch_set: dict[str, Any],
        selected_files: list[str],
        usage: ModelRunUsage,
        validation: dict[str, Any] | None = None,
        stage_outputs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.patch_set = patch_set
        self.selected_files = selected_files
        self.usage = usage
        self.validation = validation or {}
        self.stage_outputs = stage_outputs or {}


class ModelJsonResponseError(ValidationError):
    def __init__(self, message: str, *, raw_content: str, usage: ModelRunUsage) -> None:
        super().__init__(message)
        self.raw_content = raw_content
        self.usage = usage
