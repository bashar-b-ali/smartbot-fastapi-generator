from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    username: str
    first_name: str
    last_name: str
    date_joined: datetime
    email_verified: bool


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    password_confirm: str
    first_name: str = Field(default="", max_length=30)
    last_name: str = Field(default="", max_length=30)

    @model_validator(mode="after")
    def passwords_match(self) -> "UserRegister":
        if self.password != self.password_confirm:
            raise ValueError("Passwords don't match")
        return self


class RegisterResponse(BaseModel):
    message: str
    user_id: UUID
    verification_code: str | None = None


class EmailVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendVerification(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenOnly(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=128)
    new_password_confirm: str

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordResetConfirm":
        if self.new_password != self.new_password_confirm:
            raise ValueError("Passwords don't match")
        return self


class ChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)
    new_password_confirm: str

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePassword":
        if self.new_password != self.new_password_confirm:
            raise ValueError("Passwords don't match")
        return self


class UpdateProfile(BaseModel):
    first_name: str | None = Field(default=None, max_length=30)
    last_name: str | None = Field(default=None, max_length=30)


class MessageResponse(BaseModel):
    message: str
    verification_code: str | None = None
    reset_code: str | None = None
