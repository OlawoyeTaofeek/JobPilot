"""Collector run log (app/db/repositories/collector_runs.py) against real Postgres."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.repositories.collector_runs import log_run, requests_today, spent_today


def now() -> datetime:
    return datetime.now(UTC)


async def test_log_successful_run(db):
    run = await log_run(db, started_at=now(), source="jsearch", provider="rapidapi",
                        query="AI engineer in London", country="gb",
                        fetched=10, inserted=7, duplicates=3, quota_remaining=179)
    await db.refresh(run)
    assert run.id is not None
    assert run.error is None
    assert run.finished_at >= run.started_at
    assert (run.fetched, run.inserted, run.duplicates, run.skipped_bad) == (10, 7, 3, 0)


async def test_log_failed_run_keeps_error(db):
    run = await log_run(db, started_at=now(), source="jsearch", provider="rapidapi",
                        query="q", error="JSearchUnavailableError: Server error after 3 attempts")
    assert run.fetched == 0
    assert "Unavailable" in run.error


async def test_cost_is_stored_as_exact_decimal(db):
    run = await log_run(db, started_at=now(), source="linkedin", provider="anyapi",
                        query="q", cost_usd=0.0044)
    await db.refresh(run)
    assert run.cost_usd == Decimal("0.004400")


async def test_spent_today_sums_only_today_and_only_that_source(db):
    await log_run(db, started_at=now(), source="linkedin", provider="anyapi", query="a", cost_usd=0.0044)
    await log_run(db, started_at=now(), source="linkedin", provider="anyapi", query="b", cost_usd=0.0010)
    await log_run(db, started_at=now() - timedelta(days=2), source="linkedin", provider="anyapi",
                  query="old", cost_usd=1.0)                      # not today
    await log_run(db, started_at=now(), source="jsearch", provider="rapidapi", query="c")  # other source
    assert await spent_today(db, "linkedin") == Decimal("0.0054")
    assert await spent_today(db, "jsearch") == Decimal("0")
    assert await spent_today(db, "greenhouse") == Decimal("0")     # never ran


async def test_requests_today_counts_attempts(db):
    for q in ("a", "b", "c"):
        await log_run(db, started_at=now(), source="jsearch", provider="rapidapi", query=q)
    assert await requests_today(db, "jsearch") == 3