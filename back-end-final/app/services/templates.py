"""Template application service."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.project import Project, ProjectFile
from app.models.template import ProjectTemplate


def _safe_template_target(project_root: str, rel_path: str) -> Path:
    if not rel_path or "\x00" in rel_path:
        raise ValidationError("Invalid template file path")
    root = Path(project_root).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        raise ValidationError("Template file path escapes project folder")
    return target


class TemplateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def apply_to_project(self, template: ProjectTemplate, project: Project) -> None:
        """Write TemplateFile rows into a newly-created project and record metadata."""
        for template_file in template.files:
            target = _safe_template_target(project.folder_path, template_file.file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = template_file.content.encode("utf-8")
            target.write_bytes(data)
            self.db.add(
                ProjectFile(
                    project_id=project.id,
                    file_name=template_file.file_name or target.name,
                    file_path=template_file.file_path.replace("\\", "/"),
                    file_size=len(data),
                    file_type="template",
                )
            )
        await self.db.flush()
