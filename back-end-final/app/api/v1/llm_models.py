from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, get_current_admin
from app.core.crypto import encrypt_secret
from app.core.exceptions import NotFoundError
from app.models.llm_config import LLMModelConfig
from app.models.user import User
from app.repositories.llm_config import LLMModelConfigRepository
from app.schemas.llm_config import LLMModelCreate, LLMModelOut, LLMModelUpdate

router = APIRouter(prefix="/llm-models", tags=["llm-models"], dependencies=[])

AdminUser = Annotated[User, Depends(get_current_admin)]


@router.get("", response_model=list[LLMModelOut])
async def list_models(_: AdminUser, db: DbSession) -> list[LLMModelOut]:
    rows = await LLMModelConfigRepository(db).list_all()
    return [LLMModelOut.model_validate(r) for r in rows]


@router.post("", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: LLMModelCreate, _: AdminUser, db: DbSession
) -> LLMModelOut:
    repo = LLMModelConfigRepository(db)
    data = payload.model_dump()
    data["api_key"] = encrypt_secret(data.get("api_key"))
    obj = LLMModelConfig(**data)
    created = await repo.add(obj)
    if created.is_default:
        await repo.clear_default_except(created.id)
    return LLMModelOut.model_validate(created)


@router.get("/{model_id}", response_model=LLMModelOut)
async def get_model(model_id: UUID, _: AdminUser, db: DbSession) -> LLMModelOut:
    obj = await LLMModelConfigRepository(db).get_by_id(model_id)
    if not obj:
        raise NotFoundError("Model not found")
    return LLMModelOut.model_validate(obj)


@router.patch("/{model_id}", response_model=LLMModelOut)
async def update_model(
    model_id: UUID, payload: LLMModelUpdate, _: AdminUser, db: DbSession
) -> LLMModelOut:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_by_id(model_id)
    if not obj:
        raise NotFoundError("Model not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "api_key" and value is not None:
            value = encrypt_secret(value)
        setattr(obj, field, value)
    await db.flush()
    if obj.is_default:
        await repo.clear_default_except(obj.id)
    return LLMModelOut.model_validate(obj)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: UUID, _: AdminUser, db: DbSession) -> None:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_by_id(model_id)
    if not obj:
        raise NotFoundError("Model not found")
    await repo.delete(obj)


@router.post("/{model_id}/set-default", response_model=LLMModelOut)
async def set_default(model_id: UUID, _: AdminUser, db: DbSession) -> LLMModelOut:
    repo = LLMModelConfigRepository(db)
    obj = await repo.get_by_id(model_id)
    if not obj:
        raise NotFoundError("Model not found")
    obj.is_default = True
    await db.flush()
    await repo.clear_default_except(obj.id)
    return LLMModelOut.model_validate(obj)
