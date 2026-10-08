from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthError, ForbiddenError
from app.core.security import decode_token
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user_id(
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> str:
    if not token:
        raise AuthError("Authentication required")
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid token", code="invalid_token") from exc
    return payload.sub


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


async def get_current_user(
    db: DbSession, user_id: CurrentUserId
):
    # Imported lazily to avoid a circular import once models land.
    from app.repositories.user import UserRepository

    user = await UserRepository(db).get_by_id(user_id)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")
    return user


async def get_current_admin(user=Depends(get_current_user)):
    if not user.is_staff:
        raise ForbiddenError("Admin privileges required")
    return user
