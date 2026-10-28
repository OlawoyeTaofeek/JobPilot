"""Pydantic user schemas (app/schemas/user.py, app/schemas/auth.py)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.auth import Token
from app.schemas.user import PasswordChange, UserCreate, UserOut, UserUpdate

GOOD = {"email": "me@example.com", "username": "taofeek", "password": "goodpass123"}


# ---- UserOut --------------------------------------------------------------------

def test_user_out_never_exposes_password_hash():
    orm_user = SimpleNamespace(
        id=uuid.uuid4(), email="me@example.com", username="taofeek", full_name=None,
        is_active=True, created_at=datetime.now(UTC), hashed_password="$argon2id$secret",
    )
    data = UserOut.model_validate(orm_user).model_dump()
    assert "hashed_password" not in data
    assert set(data) == {"id", "email", "username", "full_name", "is_active", "created_at"}


def test_user_out_allows_missing_full_name():
    orm_user = SimpleNamespace(
        id=uuid.uuid4(), email="me@example.com", username="taofeek", full_name=None,
        is_active=True, created_at=datetime.now(UTC),
    )
    assert UserOut.model_validate(orm_user).full_name is None


# ---- UserCreate -----------------------------------------------------------------

def test_user_create_normalizes_email_and_username():
    user = UserCreate(email="  Me@Example.COM ", username=" Ade_1 ", password="goodpass123")
    assert user.email == "me@example.com"
    assert user.username == "ade_1"
    assert user.full_name is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("username", "ab"),                 # too short
        ("username", "a" * 31),             # too long
        ("username", "bad name!"),          # bad characters
        ("username", "user@29"),            # '@' would break login-by-username
        ("username", "Admin"),              # reserved (case-insensitive)
        ("password", "short1"),             # too short
        ("password", "onlyletterslong"),    # no digit
        ("password", "1234567890"),         # no letter
        ("password", "a1" * 65),            # too long (130 chars)
    ],
)
def test_user_create_rejects_bad_input(field, value):
    with pytest.raises(ValidationError):
        UserCreate(**{**GOOD, field: value})


# ---- UserUpdate / PasswordChange / Token ---------------------------------------

def test_user_update_only_dumps_fields_that_were_sent():
    assert UserUpdate(full_name="Olawoye Taofeek").model_dump(exclude_unset=True) == {
        "full_name": "Olawoye Taofeek"
    }


def test_user_update_has_no_email_or_password_fields():
    assert "email" not in UserUpdate.model_fields
    assert "password" not in UserUpdate.model_fields


def test_user_update_rejects_reserved_username():
    with pytest.raises(ValidationError):
        UserUpdate(username="admin")


def test_password_change_validates_new_password():
    assert PasswordChange(current_password="x", new_password="newpass123")
    with pytest.raises(ValidationError):
        PasswordChange(current_password="x", new_password="weak")


def test_token_defaults_to_bearer():
    assert Token(access_token="abc").model_dump() == {"access_token": "abc", "token_type": "bearer"}
