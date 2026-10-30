"""Batch extraction: turn every posting that still needs it into structured facts.

Purpose
-------
`extract.py` knows how to extract ONE posting. This module is the job that runs it over
the whole database: it finds the postings that need work, extracts them concurrently,
saves each result, and reports what it did and what it cost. It is the second stage of the
daily pipeline:

    run_daily (collect)  ->  extract_pending (understand)  ->  digest / agent / analytics

Which postings are processed
----------------------------
Selected newest first (`collected_at DESC`), capped at `extraction_max_postings_per_run`:

    always           extracted_at IS NULL                        never attempted
    --reextract-old  extraction_version != EXTRACTION_VERSION    made by an older prompt
    --retry-failed   extraction_error IS NOT NULL                failed last time

A failed extraction still sets `extracted_at`, so broken postings are NOT retried forever;
retrying them is an explicit choice (`--retry-failed`). Bumping `EXTRACTION_VERSION` in
`extract.py` plus `--reextract-old` re-runs history under a new prompt.

How it runs
-----------
- Concurrency: an `asyncio.Semaphore(extraction_max_concurrency)` keeps at most N OpenAI
  calls in flight. Database writes happen outside the semaphore (they are fast).
- Sessions: the work list is loaded in one short session; each result is saved in its own
  short session and committed immediately. No connection is held while waiting on the
  model, and a crash mid-batch keeps everything already saved.
- Tracing: the whole batch is one `extract_pending` trace (OpenAI dashboard and Langfuse),
  with each posting's run nested inside it.

Only one batch at a time
------------------------
If two batches ran together (Task Scheduler fires while you also run it by hand), both
would select the same pending postings and pay OpenAI twice for each one. To prevent that,
`run_batch` first takes a Postgres **advisory lock** (`pg_try_advisory_lock`):

- It is a named lock that lives in Postgres, not in a table, so it works across processes
  and machines that share the database (your PC today, GitHub Actions tomorrow).
- `try` means "don't wait": if another batch holds it, this run logs a message and exits
  cleanly with nothing done (`BatchTotals.skipped_locked = True`).
- It is tied to one database connection, held open for the whole batch. If the process
  crashes, Postgres closes that connection and releases the lock automatically, so a dead
  run can never leave the lock stuck.

(The alternative, `SELECT ... FOR UPDATE SKIP LOCKED`, lets several workers share one
queue in parallel. That is the right tool for many workers; one batch at a time is
simpler and enough at this scale.)

Error policy
------------
- Per-posting problems (bad output, timeouts, transient rate limits after retries) are
  recorded on the row (`extraction_error`) and counted; the batch continues.
- Transient API errors (429, 5xx, timeouts, connection drops) are retried by the OpenAI
  client itself with exponential backoff (`OPENAI_MAX_RETRIES`), so there is no custom
  retry loop here.
- Configuration errors (invalid or blocked API key, `insufficient_quota`) are raised by
  `extract_one`. `asyncio.TaskGroup` then cancels the remaining tasks and the run stops,
  instead of marking hundreds of postings as failed for the same reason.

Cost controls
-------------
- `extraction_max_postings_per_run` (Settings) is a hard cap, whatever `--limit` says.
- Tokens are summed per run and converted to an estimated USD cost with the prices in
  Settings (`extraction_price_input_per_m` / `extraction_price_output_per_m`, USD per 1M
  tokens). They live in configuration, not code, because prices change and differ per
  model: updating them is an `.env` edit, not a code change. Once the LLM gateway exists
  (LiteLLM proxy), it computes cost from its maintained price list and becomes the source
  of truth; this estimate is then only a cross-check.

Usage
-----
    uv run python -m app.collector.extract_pending                     # new postings only
    uv run python -m app.collector.extract_pending --reextract-old     # + older prompt versions
    uv run python -m app.collector.extract_pending --retry-failed      # + previous failures
    uv run python -m app.collector.extract_pending --limit 20 --dry-run  # extract, don't save

Exit code is 1 only when postings were selected and every one of them failed, so a
scheduler (Task Scheduler, GitHub Actions) can flag a broken run; otherwise 0 (including
"another batch is running", which is not an error).

From code (e.g. at the end of `run_daily`, step 5):

    totals = await run_batch(limit=100)

Testing
-------
`run_batch` takes `agent`, `session_factory` and `bind` (the engine used for the lock) as
parameters, so tests can pass an agent backed by `agents.testing.ScriptedModel` (no API
calls) and the test database, the same pattern as `run_daily`.

Related modules
---------------
- `app.collector.extract`    the agent, prompt, `extract_one`, `save_facts`
- `app.collector.facts`      the `PostingFacts` output schema
- `app.collector.skills`     skill-name normalisation for `posting_skills`
- `app.observability`        Langfuse tracing setup
- `app.config`               limits and prices (`extraction_*` settings)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agents import Agent, set_default_openai_client, trace
from openai import AsyncOpenAI
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.collector.extract import (
    EXTRACTION_MODEL,
    EXTRACTION_VERSION,
    ExtractionResult,
    build_extractor,
    extract_one,
    save_facts,
)
from app.config import get_settings
from app.db.models import Posting
from app.db.session import AsyncSessionLocal, engine
from app.observability import flush_tracing, setup_tracing

log = logging.getLogger(__name__)

OPENAI_MAX_RETRIES = 5  # the OpenAI client retries 429/5xx/timeouts with backoff for us
BATCH_LOCK_KEY = 0x4A4F4258

@dataclass
class BatchTotals:
    """Counts, tokens and estimated cost of one batch run."""

    selected: int = 0
    saved: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    skipped_locked: bool = False   # True when another batch held the lock and we did nothing
    price_input_per_m: float = 0.0   # USD per 1M input tokens (set from Settings by run_batch)
    price_output_per_m: float = 0.0  # USD per 1M output tokens

    def add(self, result: ExtractionResult) -> None:
        """Count one ExtractionResult. In a dry run, "saved" means "succeeded"."""
        if result.error:
            self.failed += 1
        else:
            self.saved += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens

    @property
    def cost_usd(self) -> float:
        """Estimated cost from token counts and the configured prices."""
        return (self.input_tokens / 1_000_000 * self.price_input_per_m
                + self.output_tokens / 1_000_000 * self.price_output_per_m)

    def summary(self) -> str:
        """One readable line, e.g. 'selected=40 saved=39 failed=1 tokens in=75,000 ...'."""
        if self.skipped_locked:
            return "skipped: another extraction batch is running"
        return (f"selected={self.selected} saved={self.saved} failed={self.failed} "
                f"tokens in={self.input_tokens:,} out={self.output_tokens:,} "
                f"cost≈${self.cost_usd:.4f}")

@asynccontextmanager
async def batch_lock(bind: AsyncEngine, key: int = BATCH_LOCK_KEY) -> AsyncIterator[bool]:
    """Try to take the batch lock; yield True if we got it, False if another batch holds it.

        async with batch_lock(engine) as got_it:
            if not got_it:
                return  # someone else is extracting

    The lock is held on a dedicated connection for the whole `async with` block and released
    on exit (or automatically by Postgres if the process dies and the connection drops).
    AUTOCOMMIT keeps that connection from sitting "idle in transaction" while it waits.
    """
    async with bind.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        got_it = bool(await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}))
        try:
            yield got_it
        finally:
            if got_it:
                await conn.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": key})

async def select_pending(
    db: AsyncSession,
    *,
    limit: int,
    reextract_old: bool = False,
    retry_failed: bool = False,
) -> list[Posting]:
    """Postings to extract, newest first.

    Always:          extracted_at IS NULL                         (never tried)
    reextract_old:   OR extraction_version != EXTRACTION_VERSION  (made by an older prompt)
    retry_failed:    OR extraction_error IS NOT NULL              (failed last time)

    A row matching several conditions still comes back once (they are OR-ed in one query).
    """
    conditions = [Posting.extracted_at.is_(None)]
    if reextract_old:
        conditions.append(Posting.extraction_version.is_distinct_from(EXTRACTION_VERSION))
    if retry_failed:
        conditions.append(Posting.extraction_error.is_not(None))

    stmt = (
        select(Posting)
        .where(or_(*conditions))
        .order_by(Posting.collected_at.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())

async def process_one(
    agent: Agent,
    posting: Posting,
    semaphore: asyncio.Semaphore,
    totals: BatchTotals,
    session_factory: async_sessionmaker[AsyncSession],
    dry_run: bool,
) -> None:
    """Extract one posting (at most N at a time) and save it in its own short session."""
    async with semaphore:  # wait for a free slot; the slot is only for the OpenAI call
        result = await extract_one(agent, posting)

    totals.add(result)  # safe without a lock: asyncio runs one task at a time between awaits
    if result.error:
        log.warning("posting %s failed: %s", posting.id, result.error)

    if dry_run:
        return
    async with session_factory() as db:
        attached = await db.get(Posting, posting.id)
        if attached is None:  # deleted while we were extracting
            return
        await save_facts(db, attached, result)
        await db.commit()

async def run_batch(
    *,
    limit: int | None = None,
    reextract_old: bool = False,
    retry_failed: bool = False,
    dry_run: bool = False,
    agent: Agent | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    bind: AsyncEngine = engine,
) -> BatchTotals:
    """Extract all pending postings. Returns the totals (handy for tests and run_daily).

    `agent`, `session_factory` and `bind` are parameters so tests can pass a scripted agent
    and the test database, exactly like run_daily's session_factory.
    """
    settings = get_settings()
    cap = settings.extraction_max_postings_per_run
    limit = cap if limit is None else min(limit, cap)  # hard cap, whatever the caller asks
    totals = BatchTotals(
        price_input_per_m=settings.extraction_price_input_per_m,
        price_output_per_m=settings.extraction_price_output_per_m,
    )

    async with batch_lock(bind) as got_it:
        if not got_it:
            log.warning("another extraction batch is running; skipping this run")
            totals.skipped_locked = True
            return totals
        
        async with session_factory() as db:
            postings = await select_pending(
                db, limit=limit, reextract_old=reextract_old, retry_failed=retry_failed
            )
        totals.selected = len(postings)
        if not postings:
            log.info("nothing to extract")
            return totals
        
        agent = agent or build_extractor()
        semaphore = asyncio.Semaphore(settings.extraction_max_concurrency)
        with trace("extract_pending", metadata={"version": EXTRACTION_VERSION,
                                                "model": EXTRACTION_MODEL,
                                                "count": str(len(postings)),
                                                "dry_run": str(dry_run)}):
            async with asyncio.TaskGroup() as tg:
                for posting in postings:
                    tg.create_task(
                        process_one(agent, posting, semaphore, totals, session_factory, dry_run)
                    )

    # 3. report
    log.info(totals.summary())
    return totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def configure_openai() -> None:
    """One OpenAI client for the whole process, with more retries than the default (2).

    The client retries rate limits (429), server errors (5xx), timeouts and connection
    errors with exponential backoff, so we don't write our own retry loop. It is also used
    for uploading traces to the OpenAI dashboard.
    """
    set_default_openai_client(
        AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=OPENAI_MAX_RETRIES)
    )


async def _main(args: argparse.Namespace) -> int:
    configure_openai()
    setup_tracing()
    try:
        totals = await run_batch(
            limit=args.limit,
            reextract_old=args.reextract_old,
            retry_failed=args.retry_failed,
            dry_run=args.dry_run,
        )
        if totals.skipped_locked:
            print("Another batch is running; nothing done.")
            return 0 
        print(totals.summary() + ("  (dry run, nothing saved)" if args.dry_run else ""))
        # exit 1 only when everything failed, so a scheduler can flag a broken run
        return 1 if totals.selected and totals.failed == totals.selected else 0
    finally:
        flush_tracing()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy.engine", "httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Extract PostingFacts for pending postings.")
    parser.add_argument("--limit", type=int, default=None,
                        help="max postings this run (never above the configured cap)")
    parser.add_argument("--reextract-old", action="store_true",
                        help=f"also re-extract rows not made by {EXTRACTION_VERSION}")
    parser.add_argument("--retry-failed", action="store_true",
                        help="also retry rows whose last extraction failed")
    parser.add_argument("--dry-run", action="store_true", help="extract but don't save")
    sys.exit(asyncio.run(_main(parser.parse_args())))