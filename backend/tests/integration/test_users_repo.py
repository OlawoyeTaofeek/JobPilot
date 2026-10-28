"""User repository against real Postgres (app/db/repositories/users.py)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import User
from app.db.repositories.users import create_user, get_by_email, get_by_username


async def make(db, email="me@example.com", username="taofeek"):
    return await create_user(db, email=email, username=username, hashed_password="$argon2id$fake")


async def test_create_user_normalizes_and_db_fills_defaults(db):
    user = await create_user(
        db, email="  Me@Example.COM ", username=" Taofeek ", hashed_password="$argon2id$fake",
        full_name="Olawoye Taofeek",
    )
    assert user.email == "me@example.com"
    assert user.username == "taofeek"
    assert isinstance(user.id, uuid.UUID)            # gen_random_uuid() ran on flush
    await db.refresh(user)
    assert user.is_active is True                    # server_default
    assert user.created_at.tzinfo is not None        # timezone-aware timestamp


async def test_get_by_email_is_case_and_space_insensitive(db):
    created = await make(db)
    assert (await get_by_email(db, "  ME@example.com ")).id == created.id


async def test_get_by_username_is_case_insensitive(db):
    created = await make(db)
    assert (await get_by_username(db, "TAOFEEK")).id == created.id


async def test_lookups_return_none_when_missing(db):
    assert await get_by_email(db, "nobody@example.com") is None
    assert await get_by_username(db, "nobody") is None


async def test_duplicate_email_is_rejected_by_database(db):
    await make(db)
    with pytest.raises(IntegrityError):
        await make(db, username="someoneelse")       # same email, different case below
    await db.rollback()


async def test_duplicate_email_different_case_is_still_rejected(db):
    await make(db)
    with pytest.raises(IntegrityError):
        await make(db, email="ME@EXAMPLE.COM", username="other")
    await db.rollback()


async def test_duplicate_username_is_rejected_by_database(db):
    await make(db)
    with pytest.raises(IntegrityError):
        await make(db, email="other@example.com")
    await db.rollback()


async def test_db_get_by_primary_key(db):
    created = await make(db)
    assert (await db.get(User, created.id)).email == "me@example.com"
