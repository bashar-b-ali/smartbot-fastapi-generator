from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, get_current_user
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.repositories.template import ProjectTemplateRepository
from app.schemas.project import ProjectCreate, ProjectOut
from app.schemas.template import (
    CreateProjectFromTemplate,
    TemplateDetail,
    TemplateFileOut,
    TemplateOut,
)
from app.services.projects import ProjectService
from app.services.templates import TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])

CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[TemplateOut])
async def list_templates(_: CurrentUser, db: DbSession) -> list[TemplateOut]:
    rows = await ProjectTemplateRepository(db).list_active()
    return [TemplateOut.model_validate(r) for r in rows]


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: UUID, _: CurrentUser, db: DbSession
) -> TemplateDetail:
    obj = await ProjectTemplateRepository(db).detail(template_id)
    if not obj:
        raise NotFoundError("Template not found")
    return TemplateDetail(
        **{
            "id": obj.id,
            "name": obj.name,
            "template_type": obj.template_type,
            "description": obj.description,
            "base_structure": obj.base_structure,
            "required_apps": obj.required_apps,
            "is_active": obj.is_active,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "sample_models": obj.sample_models,
            "sample_views": obj.sample_views,
            "sample_urls": obj.sample_urls,
            "files": [TemplateFileOut.model_validate(f) for f in obj.files],
        }
    )


@router.post(
    "/create-project", response_model=ProjectOut, status_code=status.HTTP_201_CREATED
)
async def create_project_from_template(
    payload: CreateProjectFromTemplate, user: CurrentUser, db: DbSession
) -> ProjectOut:
    template = await ProjectTemplateRepository(db).detail(payload.template_id)
    if not template:
        raise NotFoundError("Template not found")

    project = await ProjectService(db).create(
        ProjectCreate(name=payload.project_name, description=payload.description), user
    )
    await TemplateService(db).apply_to_project(template, project)
    return ProjectOut.model_validate(project)
