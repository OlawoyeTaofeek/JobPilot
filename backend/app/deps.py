"""FastAPI dependencies: DB session, current user, MCP client, rate limit.

Example:
    async def db_session() -> AsyncIterator[AsyncSession]: ...
    async def current_user(token=Depends(oauth2_scheme)) -> User: ...
"""

from collections.abc import AsyncIterator
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.security import OAuth2PasswordBearer
from app.db.session import AsyncSessionLocal



async def db_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Services decide when to commit."""
    async with AsyncSessionLocal() as session:  # closes the session on exit
        try:
            yield session
        except Exception:
            await session.rollback()  # explicit: undo any half-done work
            raise


DbSession = Annotated[AsyncSession, Depends(db_session)]


# TODO: current_user (decode JWT from security/auth.py)
from app.db.models import User
from fastapi import HTTPException, status 
from sqlalchemy.ext.asyncio import AsyncSession
from app.security.auth import decode_token 
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )

    user_id = decode_token(token)
    if user_id is None:
        raise unauthorized

    user = await db.get(User, user_id)
    if user is None or not user.is_active:   # deleted or disabled since the token was issued
        raise unauthorized

    return user


CurrentUser = Annotated[User, Depends(current_user)]

# TODO: rate_limit dependency (security/rate_limit.py)