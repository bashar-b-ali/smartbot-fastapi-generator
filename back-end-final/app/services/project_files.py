"""Filesystem operations for project folders.

All blocking IO is wrapped in `asyncio.to_thread` so it doesn't block the event loop.
Path traversal is prevented by `_safe_join`, which resolves the requested relative
path and confirms the result stays inside the project's folder.
"""
from __future__ import annotations

import asyncio
import shutil
import zipfile
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import logger
from app.models.project import Project
from app.models.user import User

# Created on project creation. Empty by design; generation writes only the files
# returned by the model-owned pipeline, and uploads use their requested target.
_DEFAULT_SUBDIRS: tuple[str, ...] = ()


def _safe_join(base: str, rel: str) -> Path:
    """Join base + rel, ensuring the result stays under base. Raises ValidationError otherwise."""
    if rel and ("\x00" in rel):
        raise ValidationError("Invalid path")
    base_path = Path(base).resolve()
    target = (base_path / rel).resolve() if rel else base_path
    if not (target == base_path or base_path in target.parents):
        raise ValidationError("Invalid path")
    return target


def _create_folder_sync(user: User, project: Project) -> str:
    base = Path(settings.projects_root) / user.username / project.project_code
    base.mkdir(parents=True, exist_ok=True)
    for sub in _DEFAULT_SUBDIRS:
        (base / sub).mkdir(exist_ok=True)
    return str(base)


async def create_project_folder(user: User, project: Project) -> str:
    return await asyncio.to_thread(_create_folder_sync, user, project)


def _list_folder_sync(folder: Path) -> list[dict]:
    if not folder.exists():
        return []
    out = []
    for entry in folder.iterdir():
        try:
            stat = entry.stat()
            out.append(
                {
                    "name": entry.name,
                    "is_directory": entry.is_dir(),
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": stat.st_mtime,
                }
            )
        except OSError:
            continue
    return out


async def list_folder(project: Project, rel: str = "") -> list[dict]:
    if not project.folder_path:
        raise NotFoundError("Project folder not found")
    target = _safe_join(project.folder_path, rel)
    if not target.exists():
        raise NotFoundError("Path not found")
    if target.is_file():
        return [{
            "name": target.name,
            "is_directory": False,
            "size": target.stat().st_size,
            "modified": target.stat().st_mtime,
        }]
    return await asyncio.to_thread(_list_folder_sync, target)


def _stats_sync(folder: Path) -> tuple[int, int, int]:
    files = folders = size = 0
    if not folder.exists():
        return files, folders, size
    for entry in folder.rglob("*"):
        try:
            if entry.is_file():
                files += 1
                size += entry.stat().st_size
            elif entry.is_dir():
                folders += 1
        except OSError:
            continue
    folders += 1  # include the root
    return files, folders, size


async def project_stats(project: Project) -> dict:
    files, folders, size = await asyncio.to_thread(_stats_sync, Path(project.folder_path))
    return {
        "total_files": files,
        "total_folders": folders,
        "total_size_bytes": size,
        "total_size_mb": round(size / (1024 * 1024), 2),
    }


def _zip_sync(project_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = project_root.resolve()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in __import__("os").walk(project_root):
            dirs.sort()
            files.sort()
            rel_root = Path(root).relative_to(project_root)
            if not files and not dirs and str(rel_root) != ".":
                zf.writestr(str(rel_root).replace("\\", "/") + "/", "")
            for f in files:
                full = Path(root) / f
                arc = (rel_root / f).as_posix() if str(rel_root) != "." else f
                zf.write(full, arc)


async def create_zip(project: Project) -> Path:
    if not project.folder_path or not Path(project.folder_path).exists():
        raise NotFoundError("Project folder does not exist")
    zip_path = Path(settings.media_root) / "temp_zips" / f"{project.project_code}.zip"
    await asyncio.to_thread(_zip_sync, Path(project.folder_path), zip_path)
    return zip_path


def _delete_sync(folder: str) -> bool:
    if not folder:
        return True
    p = Path(folder)
    if not p.exists():
        return True
    # Safety: refuse to rm anything that doesn't look like a project folder we own.
    if "projects" not in p.parts:
        logger.warning("project_folder.delete.refused", path=folder)
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


async def delete_project_folder(project: Project) -> bool:
    return await asyncio.to_thread(_delete_sync, project.folder_path)


async def cleanup_zip(zip_path: Path) -> None:
    def _rm() -> None:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("zip.cleanup.failed", path=str(zip_path), error=str(exc))

    await asyncio.to_thread(_rm)


async def read_text_file(project: Project, rel_path: str) -> str:
    if not rel_path:
        raise ValidationError("Invalid file path")
    target = _safe_join(project.folder_path, rel_path)
    if not target.exists():
        raise NotFoundError("File not found")
    if not target.is_file():
        raise ValidationError("Path is not a file")
    try:
        return await asyncio.to_thread(target.read_text, encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("File is binary and cannot be displayed as text") from exc


async def write_uploaded_file(
    project: Project, rel_dir: str, filename: str, data: bytes
) -> Path:
    target_dir = _safe_join(project.folder_path, rel_dir) if rel_dir else Path(project.folder_path)
    # Re-validate after appending the filename to block traversal via filename itself.
    target = _safe_join(str(target_dir), filename)

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    await asyncio.to_thread(_write)
    return target


def resolve_for_download(project: Project, rel_path: str) -> Path:
    if not rel_path:
        raise ValidationError("Invalid path")
    target = _safe_join(project.folder_path, rel_path)
    if not target.exists():
        raise NotFoundError("File not found")
    if not target.is_file():
        raise ValidationError("Path is not a file")
    return target

async def delete_file(project: Project, rel_path: str) -> bool:
    if not rel_path:
        raise ValidationError("Invalid file path")
    target = _safe_join(project.folder_path, rel_path)
    if not target.exists():
        raise NotFoundError("File not found")
    if not target.is_file():
        raise ValidationError("Path is not a file")

    def _delete() -> None:
        target.unlink()

    await asyncio.to_thread(_delete)
    return True