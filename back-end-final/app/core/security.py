from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import jwt
from passlib.context import CryptContext  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.core.config import settings

# Bcrypt-only verification. Unknown legacy hashes fail closed instead of raising.
pwd_context = CryptContext(schemes=["bcrypt"], default="bcrypt")


def needs_rehash(hashed: str) -> bool:
    try:
        return cast(bool, pwd_context.needs_update(hashed))
    except Exception:
        return False

TokenType = Literal["access", "refresh"]


class TokenPayload(BaseModel):
    sub: str
    type: TokenType
    exp: int
    iat: int
    jti: str | None = None


def hash_password(password: str) -> str:
    return cast(str, pwd_context.hash(password))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return cast(bool, pwd_context.verify(plain, hashed))
    except Exception:
        return False


def _create_token(subject: str | UUID, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | UUID) -> str:
    return _create_token(
        subject, "access", timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str | UUID) -> str:
    return _create_token(
        subject, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: TokenType | None = None) -> TokenPayload:
    """Raises jwt.PyJWTError subclasses on failure; callers translate to HTTP errors."""
    data = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    payload = TokenPayload(**data)
    if expected_type and payload.type != expected_type:
        raise jwt.InvalidTokenError(f"Expected {expected_type} token, got {payload.type}")
    return payload
