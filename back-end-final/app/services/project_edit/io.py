from __future__ import annotations

from pathlib import Path

from app.core.exceptions import NotFoundError, ValidationError
from app.llm.writer import WriteOutcome

MAX_EDIT_FILE_BYTES = 80_000


def safe_rel(path: str) -> str:
    rel = Path(path.replace("\\", "/")).as_posix().strip("/")
    if not rel or rel.startswith("../") or "/../" in rel or rel == "..":
        raise ValidationError(f"Unsafe edit path: {path}")
    return rel


def ensure_inside_root(root: Path, rel_path: str) -> Path:
    target = (root / safe_rel(rel_path)).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise ValidationError(f"Path escapes project root: {rel_path}")
    return target


def read_project_file(root: Path, rel_path: str) -> str:
    target = ensure_inside_root(root, rel_path)
    if not target.exists() or not target.is_file():
        raise NotFoundError(f"Target file not found: {rel_path}")
    if target.stat().st_size > MAX_EDIT_FILE_BYTES:
        raise ValidationError(f"Target file too large for edit context: {rel_path}")
    return target.read_text(encoding="utf-8")


def write_text_if_changed(root: Path, rel_path: str, content: str) -> WriteOutcome | None:
    rel = safe_rel(rel_path)
    target = ensure_inside_root(root, rel)
    old = target.read_text(encoding="utf-8") if target.exists() else None
    normalized = content.replace("\r\n", "\n")
    if old == normalized:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalized, encoding="utf-8", newline="\n")
    return WriteOutcome(path=rel, bytes_written=len(normalized.encode("utf-8")))


def delete_project_file(root: Path, rel_path: str) -> bool:
    target = ensure_inside_root(root, rel_path)
    if not target.exists():
        return False
    if not target.is_file():
        raise ValidationError(f"Path is not a file: {rel_path}")
    target.unlink()
    return True

