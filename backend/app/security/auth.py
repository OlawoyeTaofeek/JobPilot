"""Password hashing, JWT issue/verify for frontend users."""

from datetime import UTC, datetime, timedelta
from typing import Literal, Annotated
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from app.config import get_settings

ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 30
REFRESH_TOKEN_DAYS = 7

TokenType = Literal["access", "refresh"]

password_hash = PasswordHash.recommended()               # Argon2
DUMMY_HASH = password_hash.hash("dummy-password")        # login timing defence

# ---- passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)

# ---- tokens ------------------------------------------------------------------

def _create_token(user_id: UUID, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),          # JWT spec: sub must be a string
        "type": token_type,           # access vs refresh
        "iat": now,                   # issued at
        "exp": now + expires_delta,   # expiry (PyJWT checks this on decode)
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)

def create_access_token(user_id: UUID, expires_delta: timedelta | None = None) -> str:
    return _create_token(user_id, "access", expires_delta or timedelta(minutes=ACCESS_TOKEN_MINUTES))

def create_refresh_token(user_id: UUID) -> str:
    return _create_token(user_id, "refresh", timedelta(days=REFRESH_TOKEN_DAYS))

def decode_token(token: str, expected_type: TokenType = "access") -> UUID | None:
    """Return the user's UUID, or None if the token is invalid, expired or the wrong type."""
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "type"]},   # reject tokens missing these
        )
        if payload["type"] != expected_type:
            return None
        return UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None