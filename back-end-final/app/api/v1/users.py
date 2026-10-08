from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, get_current_user
from app.models.user import User
from app.schemas.user import ChangePassword, MessageResponse, UpdateProfile, UserOut
from app.services.auth import AuthService

router = APIRouter(prefix="/users", tags=["users"])


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_profile(payload: UpdateProfile, user: CurrentUser, db: DbSession) -> UserOut:
    if payload.first_name is not None:
        user.first_name = payload.first_name
    if payload.last_name is not None:
        user.last_name = payload.last_name
    await db.flush()
    return UserOut.model_validate(user)


@router.post("/me/change-password", response_model=MessageResponse)
async def change_password(
    payload: ChangePassword, user: CurrentUser, db: DbSession
) -> MessageResponse:
    await AuthService(db).change_password(user, payload)
    return MessageResponse(message="Password changed successfully")
