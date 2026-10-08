from typing import Annotated

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import DbSession
from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import create_access_token, decode_token
from app.repositories.user import UserRepository
from app.schemas.user import (
    AccessTokenOnly,
    EmailVerify,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterResponse,
    ResendVerification,
    TokenPair,
    UserOut,
    UserRegister,
)
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _access_expires_in() -> int:
    return settings.access_token_expire_minutes * 60


def _refresh_expires_in() -> int:
    return settings.refresh_token_expire_days * 24 * 60 * 60


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, bg: BackgroundTasks, db: DbSession) -> RegisterResponse:
    user, verification_code = await AuthService(db).register(payload, bg)
    return RegisterResponse(
        message="Registration successful. Please check your email for verification code.",
        user_id=user.id,
        verification_code=verification_code,
    )


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: EmailVerify, bg: BackgroundTasks, db: DbSession) -> MessageResponse:
    await AuthService(db).verify_email(payload.email, payload.code, bg)
    return MessageResponse(message="Email verified successfully. You can now login.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    payload: ResendVerification, bg: BackgroundTasks, db: DbSession
) -> MessageResponse:
    verification_code = await AuthService(db).resend_verification(payload.email, bg)
    return MessageResponse(
        message="Verification code sent successfully",
        verification_code=verification_code,
    )


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    user, access, refresh = await AuthService(db).login(payload)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=_access_expires_in(),
        refresh_expires_in=_refresh_expires_in(),
        user=UserOut.model_validate(user),
    )


@router.post("/token", response_model=TokenPair)
async def token_login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenPair:
    user, access, refresh = await AuthService(db).login(
        LoginRequest(email=form.username, password=form.password)
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=_access_expires_in(),
        refresh_expires_in=_refresh_expires_in(),
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=AccessTokenOnly)
async def refresh(payload: RefreshRequest, db: DbSession) -> AccessTokenOnly:
    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Refresh token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid refresh token", code="invalid_token") from exc

    user = await UserRepository(db).get_by_id(decoded.sub)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")

    return AccessTokenOnly(
        access_token=create_access_token(user.id),
        expires_in=_access_expires_in(),
    )


@router.post("/request-password-reset", response_model=MessageResponse)
async def request_password_reset(
    payload: PasswordResetRequest, bg: BackgroundTasks, db: DbSession
) -> MessageResponse:
    reset_code = await AuthService(db).request_password_reset(payload.email, bg)
    return MessageResponse(message="Password reset code sent", reset_code=reset_code)


@router.post("/confirm-password-reset", response_model=MessageResponse)
async def confirm_password_reset(
    payload: PasswordResetConfirm, db: DbSession
) -> MessageResponse:
    await AuthService(db).confirm_password_reset(payload)
    return MessageResponse(message="Password reset successfully")
