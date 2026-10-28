"""One row per source fetch: what happened, what it cost, what failed."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CollectorRun(TimestampMixin, Base):
    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    source: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    query: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(2))

    fetched: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    inserted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped_bad: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    from_cache: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)