"""Writer: persist a list of FileSpecs under a project root on disk.

Path safety is enforced — every FileSpec.path is resolved relative to the
provided root and confirmed to stay inside it. Intermediate directories are
created as needed.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.llm.file_spec import FileSpec

logger = logging.getLogger(__name__)


@dataclass
class WriteOutcome:
    path: str
    bytes_written: int


def _safe_join(root: Path, rel: str) -> Path:
    if not rel or "\x00" in rel:
        raise ValueError("Invalid file path")
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path escapes project root: {rel}")
    return target


def _write_one_sync(root: Path, spec: FileSpec) -> WriteOutcome:
    target = _safe_join(root, spec.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = spec.content.encode("utf-8")
    target.write_bytes(data)
    return WriteOutcome(path=str(target.relative_to(root).as_posix()), bytes_written=len(data))


def _write_all_sync(root: Path, files: list[FileSpec]) -> list[WriteOutcome]:
    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Project root no longer exists: {root}")
    return [_write_one_sync(root, f) for f in files]


async def write_project(root: str | Path, files: Iterable[FileSpec]) -> list[WriteOutcome]:
    """Write every FileSpec under `root`. Returns one WriteOutcome per file."""
    files_list = list(files)
    outcomes = await asyncio.to_thread(_write_all_sync, Path(root), files_list)
    logger.info(
        "project.write root=%s count=%d bytes=%d",
        root,
        len(outcomes),
        sum(o.bytes_written for o in outcomes),
    )
    return outcomes
