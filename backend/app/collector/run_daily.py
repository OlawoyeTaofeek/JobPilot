"""Daily collector: run every job source through one pipeline and log everything.

Purpose
-------
This is stage 1 of the daily pipeline ("collect"). It decides which searches to run today,
runs each one through every configured source, and stores new postings:

    run_daily (collect)  ->  extract_pending (understand)  ->  digest / agent / analytics

By default, after collecting, the same run also extracts facts from every new posting
(`extract_pending.run_batch`), so ONE scheduled command does both stages. Skip that with
`--no-extract`; a `--dry-run` never extracts (nothing new was saved).

For every source and every planned search:

    fetch -> normalize -> fingerprint -> upsert_many -> log_run (collector_runs)

Sources
-------
Built in `build_sources()` from the keys in Settings. A source whose key is missing is
skipped with a warning, so the collector still runs with whatever is configured:

    name          class / factory              sites                               key
    jsearch       JSearchSource                aggregator (Indeed, LinkedIn, ...)  JSEARCH_API_KEY
    ats           tavily_search.ats_source     Greenhouse, Lever, Ashby, Workable  TAVILY_API_KEY
    job_boards    tavily_search.boards_source  LinkedIn, Indeed, Glassdoor (links) TAVILY_API_KEY

Every source follows the `Source` protocol in `sources/base.py`, so `run()` never needs to
know which one it is talking to (design decision D18).

The plan
--------
`SEARCHES` lists every search we care about. `todays_plan()` takes a rotating slice of it
each day, so a small daily budget still covers every market across the week. The slice is
as long as the largest per-source budget; each source then stops at its own budget.

Budgets and failure policy (design decisions D21, D23)
------------------------------------------------------
- Before every request, `requests_today(source)` is checked against that source's daily
  budget (counted from `collector_runs`, so it holds across restarts and re-runs).
- Errors are the shared ones from `sources/base.py`; each source translates its own:
      SourceAuthError   bad/missing key       -> stop THIS source (others have their own keys)
      SourceQuotaError  out of quota/credits  -> stop this source for today
      SourceError       anything else         -> skip this search, continue
  A single unusable job (`ValueError` in normalize) is skipped and counted, never fatal.
- Every attempt, success or failure, gets one `collector_runs` row: failures are never silent.

Idempotency (design decision D19)
---------------------------------
Safe to run twice: `upsert_many` skips postings already stored (same source + source_job_id),
and the postings of a page are committed together with their run-log row.

Usage (from backend/)
---------------------
    uv run python -m app.collector.run_daily               # normal run
    uv run python -m app.collector.run_daily --dry-run     # fetch + normalize, save nothing
    uv run python -m app.collector.run_daily --limit 1     # only the first planned search
    uv run python -m app.collector.run_daily --sources ats job_boards   # only these sources
    uv run python -m app.collector.run_daily --no-extract  # collect only, skip the LLM stage

Exit code is 1 when collection fetched nothing and had errors (or no source could be
configured), or when extraction could not run at all (bad OpenAI key, no credit) or every
posting it tried failed. Partial failures are recorded (`collector_runs`, `extraction_error`)
and exit 0.

Extraction errors never undo collection: postings are already committed, and the next run's
extraction picks up anything left pending.

Related modules
---------------
- `app.collector.sources.base`           PlanItem, FetchResult, Source, shared errors
- `app.collector.sources.jsearch`        JSearch client + source
- `app.collector.sources.tavily_search`  Tavily ATS + job-board sources
- `app.collector.extract_pending`        stage 2: LLM extraction of pending postings
- `app.collector.normalize`, `dedupe`    PostingIn, fingerprint
- `app.db.repositories`                  upsert_many, log_run, requests_today
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from openai import AuthenticationError, PermissionDeniedError, RateLimitError
from tavily import AsyncTavilyClient

from app.collector.dedupe import fingerprint
from app.collector.extract_pending import BatchTotals, configure_openai, run_batch
from app.collector.sources.base import (
    PlanItem,
    Source,
    SourceAuthError,
    SourceError,
    SourceQuotaError,
)
from app.collector.sources.jsearch import JSearchClient, JSearchSource
from app.collector.sources.tavily_search import ats_source, boards_source
from app.config import get_settings
from app.db.repositories.collector_runs import log_run, requests_today
from app.db.repositories.postings import upsert_many
from app.db.session import AsyncSessionLocal, engine
from app.observability import flush_tracing, setup_tracing

logger = logging.getLogger("collector")

# ---- the plan ----------------------------------------------------------------------
# Every search you care about. todays_plan() rotates through them so each one runs
# regularly while staying inside the daily request budgets.
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
    """What one whole run did, across all sources."""

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
                    logger.warning("%s: daily request budget reached (%d/%d), stopping",
                                   source.name, used, limit)
                    break

            started_at = datetime.now(UTC)
            try:
                await process_item(source, item, totals, session_factory, dry_run)
            except SourceAuthError as e:  # this source's key is bad: none of its requests will work
                totals.errors.append(f"{source.name}: {e}")
                logger.error("%s: %s - stopping this source (check its API key)", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                break
            except SourceQuotaError as e:  # quota/credits gone: this source is done for today
                totals.errors.append(f"{source.name}: {e}")
                logger.error("%s: %s - stopping this source for today", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                break
            except SourceError as e:  # unavailable / bad response: skip just this search
                totals.errors.append(f"{source.name}: {e}")
                logger.error("%s: %s - skipping this search", source.name, e)
                if not dry_run:
                    await log_failure(session_factory, source, item, started_at, e)
                continue
    return totals


# ---- building the sources ------------------------------------------------------------


async def build_sources(stack: AsyncExitStack, wanted: set[str] | None = None) -> list[Source]:
    """Create every source whose API key is configured. Clients are closed by `stack`.

    `wanted` limits which sources are built (from --sources); None means all.
    """
    settings = get_settings()
    sources: list[Source] = []

    def want(name: str) -> bool:
        return wanted is None or name in wanted

    if want("jsearch"):
        if settings.jsearch_api_key:
            js = await stack.enter_async_context(JSearchClient(settings.jsearch_api_key))
            sources.append(JSearchSource(js))
        else:
            logger.warning("JSEARCH_API_KEY not set: skipping the jsearch source")

    if want("ats") or want("job_boards"):
        if settings.tavily_api_key:
            # Never create the Tavily client without a key: the SDK would silently switch
            # to its limited "keyless" mode instead of failing.
            tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
            stack.push_async_callback(tavily.close)
            if want("ats"):
                sources.append(ats_source(tavily))
            if want("job_boards"):
                sources.append(boards_source(tavily))
        else:
            logger.warning("TAVILY_API_KEY not set: skipping the ats and job_boards sources")

    return sources


def budgets() -> dict[str, int]:
    """Daily request budget per source name."""
    settings = get_settings()
    return {
        "jsearch": settings.jsearch_max_requests_per_day,
        "ats": settings.tavily_max_requests_per_day,
        "job_boards": settings.tavily_max_requests_per_day,
    }


# ---- command line ------------------------------------------------------------------

SOURCE_NAMES = ("jsearch", "ats", "job_boards")


@dataclass
class RunResult:
    """Both stages of one run."""

    collected: Totals
    extracted: BatchTotals | None = None   # None when extraction was skipped
    extraction_error: str | None = None     # set when extraction could not run at all


async def collect(args: argparse.Namespace) -> Totals:
    """Stage 1: build the sources and run today's plan."""
    limits = budgets()
    wanted = set(args.sources) if args.sources else None

    # One plan for all sources, as long as the largest budget; each source stops at its own.
    per_day = max(limits[name] for name in (wanted or SOURCE_NAMES))
    plan = todays_plan(datetime.now(UTC).date(), SEARCHES, per_day)
    if args.limit is not None:
        plan = plan[: args.limit]
    logger.info("plan for today: %s", [f"{i.role} in {i.place} ({i.country})" for i in plan])

    async with AsyncExitStack() as stack:  # closes every API client at the end
        sources = await build_sources(stack, wanted)
        if not sources:
            totals = Totals()
            totals.errors.append("no source is configured (check the API keys in .env)")
            return totals
        logger.info("sources: %s", [s.name for s in sources])
        return await run(sources, plan, max_requests_per_day=limits, dry_run=args.dry_run)


async def extract(result: RunResult) -> None:
    """Stage 2: extract facts for everything pending. Never undoes stage 1."""
    if not get_settings().openai_api_key:
        result.extraction_error = "OPENAI_API_KEY not set: extraction skipped"
        logger.warning(result.extraction_error)
        return
    configure_openai()
    setup_tracing()
    try:
        result.extracted = await run_batch()
    except (AuthenticationError, PermissionDeniedError, RateLimitError) as e:
        # bad key / no credit: postings are safe in the DB; the next run will extract them
        result.extraction_error = f"{type(e).__name__}: {e}"[:300]
        logger.error("extraction stopped: %s", result.extraction_error)


async def _main(args: argparse.Namespace) -> RunResult:
    try:
        result = RunResult(collected=await collect(args))
        if args.dry_run or args.no_extract:
            logger.info("extraction skipped (%s)", "dry run" if args.dry_run else "--no-extract")
        else:
            await extract(result)
        return result
    finally:
        flush_tracing()
        await engine.dispose()  # clean shutdown (avoids "Event loop is closed" on Windows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect job postings from all sources.")
    parser.add_argument("--dry-run", action="store_true", help="fetch + normalize only; save nothing")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N planned searches")
    parser.add_argument("--sources", nargs="+", choices=SOURCE_NAMES, default=None,
                        help="run only these sources (default: every configured source)")
    parser.add_argument("--no-extract", action="store_true",
                        help="collect only; don't run the LLM extraction stage afterwards")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        result = asyncio.run(_main(args))
    except SourceAuthError as e:  # e.g. a client that validates its key at startup
        logger.error("cannot start: %s", e)
        return 1

    collected = result.collected
    print(f"collect: {collected.summary()}")
    for error in collected.errors:
        print(f"  error: {error}")
    if result.extracted is not None:
        print(f"extract: {result.extracted.summary()}")
    elif result.extraction_error:
        print(f"extract: FAILED - {result.extraction_error}")

    collect_broken = bool(collected.errors) and collected.fetched == 0
    extract_broken = result.extraction_error is not None and not args.dry_run and not args.no_extract
    if result.extracted is not None:
        x = result.extracted
        extract_broken = extract_broken or (x.selected > 0 and x.failed == x.selected)
    # Exit 1 = something a scheduler should flag; partial failures are recorded and exit 0.
    return 1 if collect_broken or extract_broken else 0


if __name__ == "__main__":
    sys.exit(main())