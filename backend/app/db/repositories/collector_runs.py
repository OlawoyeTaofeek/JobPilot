"""Query helpers for the collector run log."""

from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CollectorRun


async def log_run(
    db: AsyncSession,
    *,
    started_at: datetime,
    source: str,
    provider: str,
    query: str,
    country: str | None = None,
    fetched: int = 0,
    inserted: int = 0,
    duplicates: int = 0,
    skipped_bad: int = 0,
    cost_usd: float | Decimal | None = None,
    quota_remaining: int | None = None,
    from_cache: bool | None = None,
    error: str | None = None,
) -> CollectorRun:
    """Record one fetch attempt (success OR failure). Flushes, does not commit."""
    run = CollectorRun(
        started_at=started_at,
        finished_at=datetime.now(UTC),
        source=source,
        provider=provider,
        query=query[:255],
        country=country,
        fetched=fetched,
        inserted=inserted,
        duplicates=duplicates,
        skipped_bad=skipped_bad,
        cost_usd=Decimal(str(cost_usd)) if cost_usd is not None else None,
        quota_remaining=quota_remaining,
        from_cache=from_cache,
        error=error,
    )
    db.add(run)
    await db.flush()
    return run


async def spent_today(db: AsyncSession, source: str) -> Decimal:
    """Total cost_usd for `source` since midnight UTC (0 if nothing yet). Used for daily budgets."""
    midnight = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    total = await db.scalar(
        select(func.coalesce(func.sum(CollectorRun.cost_usd), 0)).where(
            CollectorRun.source == source,
            CollectorRun.started_at >= midnight,
        )
    )
    return Decimal(total)


async def requests_today(db: AsyncSession, source: str) -> int:
    """How many fetches `source` has made since midnight UTC. Used for request budgets."""
    midnight = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return await db.scalar(
        select(func.count()).select_from(CollectorRun).where(
            CollectorRun.source == source,
            CollectorRun.started_at >= midnight,
        )
    )