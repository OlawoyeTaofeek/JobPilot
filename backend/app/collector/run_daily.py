"""Daily collector: fetch -> normalize -> fingerprint -> upsert -> log.

Run from backend/:
    uv run python -m app.collector.run_daily              # normal run
    uv run python -m app.collector.run_daily --dry-run    # fetch + normalize, save nothing
    uv run python -m app.collector.run_daily --limit 1    # only the first planned search

Every fetch attempt (success or failure) gets one row in collector_runs.
Safe to run twice: postings already stored are skipped by upsert_many.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.collector.dedupe import fingerprint
from app.collector.sources.base import PlanItem, Source
from app.collector.sources.jsearch import (
    JSearchAuthError,
    JSearchClient,
    JSearchError,
    JSearchQuotaError,
    JSearchSource,
)
from app.config import get_settings
from app.db.repositories.collector_runs import log_run, requests_today
from app.db.repositories.postings import upsert_many
from app.db.session import AsyncSessionLocal, engine

logger = logging.getLogger("collector")

# ---- the plan ----------------------------------------------------------------------
# Every search you care about. todays_plan() rotates through them so each one runs
# regularly while staying inside the daily request budget.
SEARCHES: list[PlanItem] = [
    PlanItem("machine learning engineer", "Lagos", "ng"),
    PlanItem("AI engineer", "Nigeria", "ng"),
    PlanItem("data scientist", "Lagos", "ng"),
    PlanItem("machine learning engineer", "London", "gb"),
    PlanItem("AI engineer", "London", "gb"),
    PlanItem("data scientist", "Dublin", "ie"),
    PlanItem("machine learning engineer", "Berlin", "de"),
    PlanItem("AI engineer", "Amsterdam", "nl"),
    PlanItem("machine learning engineer", "Toronto", "ca"),
    PlanItem("AI engineer", "Dubai", "ae"),
    PlanItem("data engineer", "London", "gb"),
    PlanItem("MLOps engineer", "Berlin", "de"),
]


def todays_plan(today: date, searches: list[PlanItem], per_day: int) -> list[PlanItem]:
    """Pick `per_day` searches for `today`, rotating so every search gets its turn."""
    if not searches or per_day <= 0:
        return []
    per_day = min(per_day, len(searches))
    start = (today.toordinal() * per_day) % len(searches)
    return [searches[(start + i) % len(searches)] for i in range(per_day)]


# ---- tallies -----------------------------------------------------------------------


@dataclass
class Totals:
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped_bad: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} inserted={self.inserted} duplicates={self.duplicates} "
            f"skipped_bad={self.skipped_bad} cost=${self.cost_usd:.4f} errors={len(self.errors)}"
        )


# ---- one plan item -----------------------------------------------------------------


async def process_item(
    source: Source,
    item: PlanItem,
    totals: Totals,
    session_factory: Callable[[], Any],
    dry_run: bool,
) -> None:
    """Fetch one page, save new postings and log the run. Source errors propagate to the caller."""
    started_at = datetime.now(UTC)
    result = await source.fetch(item)

    pairs = []
    skipped_bad = 0
    for raw in result.raw_jobs:
        try:
            posting = source.normalize(raw, item)
        except ValueError as e:  # one bad job never sinks the page
            skipped_bad += 1
            logger.warning("skipping bad job from %s: %s", source.name, e)
            continue
        pairs.append((posting, fingerprint(posting)))

    inserted = 0
    if not dry_run:
        async with session_factory() as db:  # short session per page
            inserted = await upsert_many(db, pairs)
            await log_run(
                db,
                started_at=started_at,
                source=source.name,
                provider=result.provider,
                query=result.query,
                country=item.country,
                fetched=len(result.raw_jobs),
                inserted=inserted,
                duplicates=len(pairs) - inserted,
                skipped_bad=skipped_bad,
                cost_usd=result.cost_usd,
                quota_remaining=result.quota_remaining,
                from_cache=result.from_cache,
            )
            await db.commit()  # postings + their log row: saved together or not at all

    totals.fetched += len(result.raw_jobs)
    totals.inserted += inserted
    totals.duplicates += len(pairs) - inserted
    totals.skipped_bad += skipped_bad
    totals.cost_usd += result.cost_usd or 0.0
    logger.info(
        "%s %r: fetched=%d new=%d dup=%d bad=%d quota=%s%s",
        source.name, result.query, len(result.raw_jobs), inserted, len(pairs) - inserted,
        skipped_bad, result.quota_remaining, " (dry run)" if dry_run else "",
    )


async def log_failure(
    session_factory: Callable[[], Any],
    source: Source,
    item: PlanItem,
    started_at: datetime,
    error: Exception,
) -> None:
    """A failed fetch still gets a collector_runs row, so failures are never silent."""
    async with session_factory() as db:
        await log_run(
            db,
            started_at=started_at,
            source=source.name,
            provider=getattr(source, "provider", "unknown"),
            query=f"{item.role} in {item.place}",
            country=item.country,
            quota_remaining=getattr(error, "quota_remaining", None),
            error=f"{type(error).__name__}: {error}",
        )
        await db.commit()


# ---- the whole run -----------------------------------------------------------------


async def run(
    sources: list[Source],
    plan: list[PlanItem],
    *,
    max_requests_per_day: dict[str, int],
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    dry_run: bool = False,
) -> Totals:
    """Run every plan item through every source, respecting budgets and error types."""
    totals = Totals()

    for source in sources:
        limit = max_requests_per_day.get(source.name)
        for item in plan:
            # Budget check BEFORE spending a request.
            if limit is not None and not dry_run:
                async with session_factory() as db:
                    used = await requests_today(db, source.name)
                if used >= limit:
                    logger.warning("%s: daily request budget reached (%d/%d), stopping", source.name, used, limit)
                    break

            started_at = datetime.now(UTC)
            try:
                await process_item(source, item, totals, session_factory, dry_run)
            except JSearchAuthError as e:  # bad key: nothing will work -> stop everything
                totals.errors.append(str(e))
                logger.error("%s: %s - stopping the whole run", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                return totals
            except JSearchQuotaError as e:  # quota low/gone: this source is done for today
                totals.errors.append(str(e))
                logger.error("%s: %s - stopping this source for today", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                break
            except JSearchError as e:  # unavailable / bad response: skip just this search
                totals.errors.append(str(e))
                logger.error("%s: %s - skipping this search", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                continue
    return totals


# ---- command line ------------------------------------------------------------------


async def _main(args: argparse.Namespace) -> Totals:
    settings = get_settings()
    plan = todays_plan(datetime.now(UTC).date(), SEARCHES, settings.jsearch_max_requests_per_day)
    if args.limit is not None:
        plan = plan[: args.limit]
    logger.info("plan for today: %s", [f"{i.role} in {i.place} ({i.country})" for i in plan])

    try:
        async with JSearchClient(settings.jsearch_api_key) as js:
            sources: list[Source] = [JSearchSource(js)]  # later: LinkedIn (treg), Greenhouse, ...
            return await run(
                sources,
                plan,
                max_requests_per_day={"jsearch": settings.jsearch_max_requests_per_day},
                dry_run=args.dry_run,
            )
    finally:
        await engine.dispose()  # clean shutdown (avoids "Event loop is closed" on Windows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect job postings from all sources.")
    parser.add_argument("--dry-run", action="store_true", help="fetch + normalize only; save nothing")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N planned searches")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        totals = asyncio.run(_main(args))
    except JSearchAuthError as e:  # e.g. missing key, raised before any request
        logger.error("cannot start: %s", e)
        return 1

    print(totals.summary())
    # Exit 1 only if nothing worked at all; partial failures are recorded in collector_runs.
    return 1 if totals.errors and totals.fetched == 0 else 0


if __name__ == "__main__":
    sys.exit(main())