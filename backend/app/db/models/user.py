"""users, oauth_credentials (tokens encrypted with security/crypto.py)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))


class OAuthCredential(TimestampMixin, Base):
    """A user's connection to an outside service (Gmail, MCP provider...).

    Token columns end in _enc: they hold Fernet ciphertext from security/crypto.py.
    Always decrypt() right before use, never log the result.
    """

    __tablename__ = "oauth_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)  # one connection per provider

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50))               # "google", "mcp", ...
    account_email: Mapped[str | None] = mapped_column(String(320))  # which Gmail was connected
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)     # some providers don't issue one
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))