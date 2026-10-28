from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    normalized = email.strip().lower()
    stmt = select(User).where(User.email == normalized)
    return await db.scalar(stmt)

async def get_by_username(db: AsyncSession, username: str) -> User | None:
    normalized = username.strip().lower()
    stmt = select(User).where(User.username == normalized)
    return await db.scalar(stmt)

async def create_user(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    hashed_password: str,
    full_name: str | None = None,
) -> User:
    user = User(
        email=email.strip().lower(),
        username=username.strip().lower(),
        hashed_password=hashed_password,
        full_name=full_name,
    )
    db.add(user)
    await db.flush()      # INSERT now: id assigned, duplicates detected here
    return user