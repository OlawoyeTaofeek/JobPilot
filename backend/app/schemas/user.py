"""API schemas for users: what clients may send in, and what the API sends out.

Kept separate from the SQLAlchemy User model so internal fields
(like hashed_password) can never leak into a response.
"""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

USERNAME_PATTERN = r"^[a-zA-Z0-9_.]+$"
RESERVED_USERNAMES = {"admin", "api", "me", "root", "support", "system"}
PASSWORD_MIN, PASSWORD_MAX = 10, 128


def _check_username(v: str) -> str:
    v = v.lower()
    if v in RESERVED_USERNAMES:
        raise ValueError("This username is reserved.")
    return v


# ---- output -------------------------------------------------------------------

class UserOut(BaseModel):
    """What the API returns about a user. No password hash, ever."""

    model_config = ConfigDict(from_attributes=True)  # read straight from a SQLAlchemy User

    id: UUID
    email: EmailStr
    username: str
    full_name: str | None
    is_active: bool
    created_at: datetime


# ---- input --------------------------------------------------------------------

class UserCreate(BaseModel):
    """Register request body."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    username: str = Field(min_length=3, max_length=30, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return _check_username(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        # Length is the main rule (NIST); also require at least one letter and one digit.
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v


class UserUpdate(BaseModel):
    """Profile edit. Send only the fields you want to change.

    Email and password are deliberately NOT here: they need their own,
    verified flows (see PasswordChange).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=USERNAME_PATTERN)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        return _check_username(v) if v is not None else v


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v