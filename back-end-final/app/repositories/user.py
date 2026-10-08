from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.models.user import EmailVerification, PasswordReset, User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()


class EmailVerificationRepository(BaseRepository[EmailVerification]):
    model = EmailVerification

    async def find_active(self, user_id: UUID, code: str) -> EmailVerification | None:
        now = datetime.now(UTC)
        stmt = select(EmailVerification).where(
            EmailVerification.user_id == user_id,
            EmailVerification.code == code,
            EmailVerification.is_used.is_(False),
            EmailVerification.expires_at > now,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()


class PasswordResetRepository(BaseRepository[PasswordReset]):
    model = PasswordReset

    async def find_active(self, user_id: UUID, code: str) -> PasswordReset | None:
        now = datetime.now(UTC)
        stmt = select(PasswordReset).where(
            PasswordReset.user_id == user_id,
            PasswordReset.code == code,
            PasswordReset.is_used.is_(False),
            PasswordReset.expires_at > now,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
