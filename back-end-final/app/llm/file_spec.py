"""Shared file-write types for model-owned project generation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileSpec:
    """A single file to be written under the generated project root."""

    path: str
    content: str

    def __post_init__(self) -> None:
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(f"unsafe FileSpec path: {self.path!r}")
