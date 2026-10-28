"""Login, logout, OAuth callbacks (Gmail, MCP provider).

HTTP layer only: validate input, call a service, return a response.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repositories.users import get_by_email, get_by_username
from app.deps import CurrentUser, DbSession
from app.schemas.auth import Token
from app.schemas.user import UserOut
from app.security.auth import DUMMY_HASH, create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# TODO: add endpoints, e.g.
# @router.get("")
# async def list_items(user=Depends(current_user)): ...

async def _find_user(db: AsyncSession, identifier: str) -> User | None:
    """Look up by email if it contains '@', otherwise by username."""
    if "@" in identifier:
        return await get_by_email(db, identifier)
    return await get_by_username(db, identifier)

@router.post("/login", response_model=Token)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> Token:
    user = await _find_user(db, form.username)

    hashed = user.hashed_password if user else DUMMY_HASH
    password_ok = verify_password(form.password, hashed)

    if not user or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/username or password",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return Token(access_token=create_access_token(user_id=user.id))

@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    """Return the logged-in user. current_user already verified the token."""
    return user