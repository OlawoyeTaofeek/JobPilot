"""Batch extraction: turn every posting that still needs it into structured facts.

Purpose
-------
`extractor.py` knows how to extract ONE posting. This module is the job that runs it over
the whole database: it finds the postings that need work, extracts them concurrently,
saves each result, and reports what it did and what it cost. It is the second stage of the
daily pipeline:

    run_daily (collect)  ->  extract_pending (understand)  ->  digest / agent / analytics

Which postings are processed
----------------------------
Selected newest first (`collected_at DESC`), capped at `MAX_POSTINGS_PER_RUN`:

    always           extracted_at IS NULL                        never attempted
    --reextract-old  extraction_version != EXTRACTION_VERSION    made by an older prompt
    --retry-failed   extraction_error IS NOT NULL                failed last time

A failed extraction still sets `extracted_at`, so broken postings are NOT retried forever;
retrying them is an explicit choice (`--retry-failed`). Bumping `EXTRACTION_VERSION` in
`extractor.py` plus `--reextract-old` re-runs history under a new prompt.

How it runs
-----------
- Concurrency: an `asyncio.Semaphore(MAX_CONCURRENCY)` keeps at most N OpenAI calls in
  flight. Database writes happen outside the semaphore (they are fast).
- Sessions: the work list is loaded in one short session; each result is saved in its own
  short session and committed immediately. No connection is held while waiting on the
  model, and a crash mid-batch keeps everything already saved.
- Tracing: the whole batch is one `extract_pending` trace (OpenAI dashboard and Langfuse),
  with each posting's run nested inside it.

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
- `MAX_POSTINGS_PER_RUN` is a hard cap, whatever `--limit` says.
- Tokens are summed per run and converted to an estimated USD cost using
  `PRICE_INPUT_PER_M` / `PRICE_OUTPUT_PER_M` (keep these in line with OpenAI's pricing).

Usage
-----
    uv run python -m app.collector.extract_pending                     # new postings only
    uv run python -m app.collector.extract_pending --reextract-old     # + older prompt versions
    uv run python -m app.collector.extract_pending --retry-failed      # + previous failures
    uv run python -m app.collector.extract_pending --limit 20 --dry-run  # extract, don't save

Exit code is 1 only when postings were selected and every one of them failed, so a
scheduler (Task Scheduler, GitHub Actions) can flag a broken run; otherwise 0.

From code (e.g. at the end of `run_daily`, step 5):

    totals = await run_batch(limit=100)

Testing
-------
`run_batch` takes `agent` and `session_factory` as parameters, so tests can pass an agent
backed by `agents.testing.ScriptedModel` (no API calls) and the test database's session
factory, the same pattern as `run_daily`.

Related modules
---------------
- `app.collector.extractor`  the agent, prompt, `extract_one`, `save_facts`
- `app.collector.facts`      the `PostingFacts` output schema
- `app.collector.skills`     skill-name normalisation for `posting_skills`
- `app.observability`        Langfuse tracing setup
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass

from agents import Agent, set_default_openai_client, trace
from openai import AsyncOpenAI
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

MAX_CONCURRENCY = 5          # postings in flight at once
MAX_POSTINGS_PER_RUN = 200   # hard cost cap per run, whatever --limit says
OPENAI_MAX_RETRIES = 5       # the OpenAI client retries 429/5xx/timeouts with backoff for us

# USD per 1M tokens for EXTRACTION_MODEL. Check https://openai.com/api/pricing and update.
PRICE_INPUT_PER_M = 0.25
PRICE_OUTPUT_PER_M = 2.00


# ---------------------------------------------------------------------------
# results of one batch
# ---------------------------------------------------------------------------

@dataclass
class BatchTotals:
    selected: int = 0
    saved: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, result: ExtractionResult) -> None:
        """Count one ExtractionResult."""
        # TODO: failed += 1 if result.error else saved += 1   (in a dry run "saved" = "succeeded")
        # TODO: add result.input_tokens / result.output_tokens to the totals
        raise NotImplementedError

    @property
    def cost_usd(self) -> float:
        # TODO: input_tokens / 1_000_000 * PRICE_INPUT_PER_M + same for output
        raise NotImplementedError

    def summary(self) -> str:
        # TODO: one line, e.g.
        # "selected=40 saved=39 failed=1 tokens in=75,000 out=25,000 cost≈$0.07"
        raise NotImplementedError


# ---------------------------------------------------------------------------
# which postings need work
# ---------------------------------------------------------------------------

async def select_pending(
    db: AsyncSession,
    *,
    limit: int,
    reextract_old: bool = False,
    retry_failed: bool = False,
) -> list[Posting]:
    """Postings to extract, newest first.

    Always:          extracted_at IS NULL                     (never tried)
    reextract_old:   OR extraction_version != EXTRACTION_VERSION  (made by an older prompt)
    retry_failed:    OR extraction_error IS NOT NULL           (failed last time)
    """
    # TODO:
    # 1. conditions = [Posting.extracted_at.is_(None)]
    # 2. append the other two conditions when their flag is True
    # 3. stmt = select(Posting).where(or_(*conditions))
    #        .order_by(Posting.collected_at.desc())
    #        .limit(min(limit, MAX_POSTINGS_PER_RUN))
    # 4. return list((await db.scalars(stmt)).all())
    # Note: rows from an older version that ALSO failed match two conditions; or_() means
    #       each row still comes back once.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# one posting, inside the concurrency limit
# ---------------------------------------------------------------------------

async def process_one(
    agent: Agent,
    posting: Posting,
    semaphore: asyncio.Semaphore,
    totals: BatchTotals,
    session_factory: async_sessionmaker[AsyncSession],
    dry_run: bool,
) -> None:
    """Extract one posting (max MAX_CONCURRENCY at a time) and save it in its own session."""
    # TODO:
    # async with semaphore:                      # wait for a free slot
    #     result = await extract_one(agent, posting)
    # (Release the slot BEFORE saving: the DB write is fast; the slot is for OpenAI calls.)
    #
    # totals.add(result)
    # if result.error: log.warning(...) with posting.id
    #
    # if not dry_run:
    #     async with session_factory() as db:
    #         attached = await db.get(Posting, posting.id)
    #         if attached is None: return        # deleted meanwhile
    #         await save_facts(db, attached, result)
    #         await db.commit()
    raise NotImplementedError


# ---------------------------------------------------------------------------
# the batch
# ---------------------------------------------------------------------------

async def run_batch(
    *,
    limit: int = MAX_POSTINGS_PER_RUN,
    reextract_old: bool = False,
    retry_failed: bool = False,
    dry_run: bool = False,
    agent: Agent | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> BatchTotals:
    """Extract all pending postings. Returns the totals (handy for tests and run_daily).

    `agent` and `session_factory` are parameters so tests can pass a scripted agent and the
    test database, exactly like run_daily's session_factory.
    """
    totals = BatchTotals()
    agent = agent or build_extractor()

    # TODO 1: load the work list in a short session:
    #   async with session_factory() as db:
    #       postings = await select_pending(db, limit=..., reextract_old=..., retry_failed=...)
    #   totals.selected = len(postings); if none: log.info("nothing to extract") and return totals

    # TODO 2: run them concurrently, all inside ONE trace:
    #   semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    #   with trace("extract_pending", metadata={"version": EXTRACTION_VERSION,
    #                                           "model": EXTRACTION_MODEL,
    #                                           "count": str(len(postings))}):
    #       async with asyncio.TaskGroup() as tg:
    #           for p in postings:
    #               tg.create_task(process_one(agent, p, semaphore, totals, session_factory, dry_run))
    #
    #   Why TaskGroup (Python 3.11+) and not gather: if one task raises (e.g. a bad API key
    #   from extract_one), TaskGroup CANCELS the others and re-raises, so the batch stops
    #   fast instead of firing 199 more doomed requests.

    # TODO 3: log.info(totals.summary()) and return totals
    raise NotImplementedError


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def configure_openai() -> None:
    """One OpenAI client for the whole process, with more retries than the default (2).

    The client retries rate limits (429), server errors (5xx), timeouts and connection
    errors with exponential backoff, so we don't write our own retry loop.
    """
    # TODO: set_default_openai_client(
    #           AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=OPENAI_MAX_RETRIES))
    raise NotImplementedError


async def _main(args: argparse.Namespace) -> int:
    configure_openai()
    setup_tracing()
    try:
        # TODO: totals = await run_batch(limit=args.limit, reextract_old=args.reextract_old,
        #                                retry_failed=args.retry_failed, dry_run=args.dry_run)
        # TODO: print(totals.summary()) plus "(dry run, nothing saved)" when dry_run
        # TODO: return 1 if totals.selected and totals.failed == totals.selected else 0
        #       (exit code 1 = everything failed -> Task Scheduler / CI can flag it)
        raise NotImplementedError
    finally:
        flush_tracing()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy.engine", "httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Extract PostingFacts for pending postings.")
    parser.add_argument("--limit", type=int, default=MAX_POSTINGS_PER_RUN)
    parser.add_argument("--reextract-old", action="store_true",
                        help=f"also re-extract rows not made by {EXTRACTION_VERSION}")
    parser.add_argument("--retry-failed", action="store_true",
                        help="also retry rows whose last extraction failed")
    parser.add_argument("--dry-run", action="store_true", help="extract but don't save")
    sys.exit(asyncio.run(_main(parser.parse_args())))