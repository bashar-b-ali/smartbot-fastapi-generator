import random
import string
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError, ConflictError, NotFoundError, ValidationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.user import EmailVerification, PasswordReset, User
from app.repositories.user import (
    EmailVerificationRepository,
    PasswordResetRepository,
    UserRepository,
)
from app.schemas.user import (
    ChangePassword,
    LoginRequest,
    PasswordResetConfirm,
    UserRegister,
)
from app.services import email as email_service

CODE_TTL = timedelta(minutes=10)
DUMMY_PASSWORD_HASH = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8fG3XRNCw0hKjW8h3xWZQ5HztFHG3m"


def _generate_username() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=10))


def _generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _dev_exposed_code(code: str) -> str | None:
    if settings.is_production:
        return None
    if settings.smtp_user and settings.smtp_password:
        return None
    return code


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserRepository(db)
        self.verifications = EmailVerificationRepository(db)
        self.resets = PasswordResetRepository(db)

    async def register(self, payload: UserRegister, bg: BackgroundTasks) -> tuple[User, str | None]:
        email = _normalize_email(payload.email)
        if await self.users.get_by_email(email):
            raise ConflictError("Email already registered", code="email_taken")

        # Username clashes are extremely unlikely (10 alnum chars) but cheap to guard against.
        for _ in range(5):
            username = _generate_username()
            if not await self.users.get_by_username(username):
                break
        else:
            raise ConflictError("Could not allocate username, please retry")

        user = User(
            email=email,
            username=username,
            password=hash_password(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
            is_active=False,
            email_verified=False,
        )
        await self.users.add(user)

        code = _generate_code()
        await self.verifications.add(
            EmailVerification(
                user_id=user.id,
                code=code,
                expires_at=datetime.now(UTC) + CODE_TTL,
            )
        )
        await self.db.commit()
        bg.add_task(email_service.send_verification_email, user.email, code)
        return user, _dev_exposed_code(code)

    async def verify_email(self, email: str, code: str, bg: BackgroundTasks) -> None:
        user = await self.users.get_by_email(_normalize_email(email))
        if not user:
            raise NotFoundError("User not found")
        verification = await self.verifications.find_active(user.id, code)
        if not verification:
            raise ValidationError("Invalid or expired verification code")

        user.is_active = True
        user.email_verified = True
        verification.is_used = True
        await self.db.flush()
        await self.db.commit()

        bg.add_task(email_service.send_welcome_email, user.email, user.username)

    async def resend_verification(self, email: str, bg: BackgroundTasks) -> str | None:
        user = await self.users.get_by_email(_normalize_email(email))
        if not user:
            raise NotFoundError("User not found")
        if user.email_verified:
            raise ValidationError("Email is already verified")

        code = _generate_code()
        await self.verifications.add(
            EmailVerification(
                user_id=user.id,
                code=code,
                expires_at=datetime.now(UTC) + CODE_TTL,
            )
        )
        await self.db.commit()
        bg.add_task(email_service.send_verification_email, user.email, code)
        return _dev_exposed_code(code)

    async def login(self, payload: LoginRequest) -> tuple[User, str, str]:
        user = await self.users.get_by_email(_normalize_email(payload.email))
        password_hash = user.password if user else DUMMY_PASSWORD_HASH
        if not verify_password(payload.password, password_hash) or not user:
            raise AuthError("Invalid email or password")
        if not user.is_active:
            raise AuthError("Account is not activated", code="account_inactive")

        if needs_rehash(user.password):
            user.password = hash_password(payload.password)

        user.last_login = datetime.now(UTC)
        await self.db.flush()

        return user, create_access_token(user.id), create_refresh_token(user.id)

    async def request_password_reset(self, email: str, bg: BackgroundTasks) -> str | None:
        user = await self.users.get_by_email(_normalize_email(email))
        if not user:
            raise NotFoundError("User not found")
        code = _generate_code()
        await self.resets.add(
            PasswordReset(
                user_id=user.id,
                code=code,
                expires_at=datetime.now(UTC) + CODE_TTL,
            )
        )
        await self.db.commit()
        bg.add_task(email_service.send_password_reset_email, user.email, code)
        return _dev_exposed_code(code)

    async def confirm_password_reset(self, payload: PasswordResetConfirm) -> None:
        user = await self.users.get_by_email(_normalize_email(payload.email))
        if not user:
            raise NotFoundError("User not found")
        reset = await self.resets.find_active(user.id, payload.code)
        if not reset:
            raise ValidationError("Invalid or expired reset code")

        user.password = hash_password(payload.new_password)
        reset.is_used = True
        await self.db.flush()

    async def change_password(self, user: User, payload: ChangePassword) -> None:
        if not verify_password(payload.old_password, user.password):
            raise ValidationError("Current password is incorrect")
        user.password = hash_password(payload.new_password)
        await self.db.flush()
