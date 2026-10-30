"""run_daily end-to-end against real Postgres, with a scripted fake source (no network)."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from app.collector.normalize import normalize_jsearch
from app.collector.run_daily import run
from app.collector.sources.base import FetchResult, PlanItem
from app.collector.sources.jsearch import (
    JSearchAuthError,
    JSearchQuotaError,
    JSearchUnavailableError,
)
from app.db.models import CollectorRun, Posting

LONDON = PlanItem("AI engineer", "London", "gb")
LAGOS = PlanItem("ML engineer", "Lagos", "ng")
DUBLIN = PlanItem("data scientist", "Dublin", "ie")


class ScriptedSource:
    """A fake Source: each fetch returns (or raises) the next scripted item."""

    name = "jsearch"
    provider = "rapidapi"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def fetch(self, item):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FetchResult(raw_jobs=step, provider=self.provider,
                           query=f"{item.role} in {item.place}", quota_remaining=150)

    def normalize(self, raw, item):
        return normalize_jsearch(raw, f"{item.role} in {item.place}")


@pytest.fixture
def session_factory(db):
    """run_daily opens 'new' sessions; in tests they all reuse the rolled-back test session."""
    @asynccontextmanager
    async def factory():
        yield db
    return factory


@pytest.fixture
def jobs(jsearch_page_ng):
    return jsearch_page_ng["data"]["jobs"]  # 3 real jobs


async def count(db, model, *where):
    return await db.scalar(select(func.count()).select_from(model).where(*where))


async def test_first_run_inserts_and_logs(db, session_factory, jobs):
    totals = await run([ScriptedSource([jobs])], [LONDON],
                       max_requests_per_day={"jsearch": 6}, session_factory=session_factory)
    assert (totals.fetched, totals.inserted, totals.duplicates) == (3, 3, 0)
    assert await count(db, Posting) == 3
    log = (await db.scalars(select(CollectorRun))).one()
    assert (log.fetched, log.inserted, log.error) == (3, 3, None)
    assert log.query == "AI engineer in London" and log.quota_remaining == 150


async def test_second_run_inserts_nothing(db, session_factory, jobs):
    await run([ScriptedSource([jobs])], [LONDON], max_requests_per_day={}, session_factory=session_factory)
    totals = await run([ScriptedSource([jobs])], [LONDON], max_requests_per_day={}, session_factory=session_factory)
    assert (totals.inserted, totals.duplicates) == (0, 3)
    assert await count(db, Posting) == 3
    assert await count(db, CollectorRun) == 2              # both attempts logged


async def test_bad_job_is_skipped_not_fatal(db, session_factory, jobs):
    broken = {**jobs[0], "job_uid": None}
    totals = await run([ScriptedSource([[broken, *jobs[1:]]])], [LONDON],
                       max_requests_per_day={}, session_factory=session_factory)
    assert (totals.skipped_bad, totals.inserted) == (1, 2)


async def test_unavailable_skips_only_that_search_and_logs_it(db, session_factory, jobs):
    source = ScriptedSource([JSearchUnavailableError("Server error after 3 attempts"), jobs])
    totals = await run([source], [LONDON, LAGOS], max_requests_per_day={}, session_factory=session_factory)
    assert source.calls == 2                                # moved on to the next search
    assert totals.inserted == 3 and len(totals.errors) == 1
    failed = (await db.scalars(select(CollectorRun).where(CollectorRun.error.is_not(None)))).one()
    assert failed.fetched == 0 and "JSearchUnavailableError" in failed.error


async def test_quota_error_stops_the_source(db, session_factory, jobs):
    source = ScriptedSource([JSearchQuotaError("Quota low", quota_remaining=8), jobs, jobs])
    totals = await run([source], [LONDON, LAGOS, DUBLIN], max_requests_per_day={}, session_factory=session_factory)
    assert source.calls == 1                                # nothing after the quota error
    assert totals.fetched == 0
    failed = (await db.scalars(select(CollectorRun))).one()
    assert failed.quota_remaining == 8


async def test_auth_error_stops_only_that_source(db, session_factory, jobs, caplog):
    """A bad key stops ITS source; other sources have their own keys and keep going."""
    first = ScriptedSource([JSearchAuthError("Rejected", status_code=403), jobs])
    second = ScriptedSource([jobs, []])
    second.name = "other"
    totals = await run([first, second], [LONDON, LAGOS], max_requests_per_day={},
                       session_factory=session_factory)
    assert first.calls == 1                                 # no second request with a bad key
    assert second.calls == 2                                # the other source still ran
    assert "Rejected" in totals.errors[0]
    assert totals.inserted == 3


async def test_daily_budget_stops_before_spending(db, session_factory, jobs):
    source = ScriptedSource([jobs, jobs, jobs])
    await run([source], [LONDON, LAGOS, DUBLIN], max_requests_per_day={"jsearch": 2},
              session_factory=session_factory)
    assert source.calls == 2                                # third request never sent


async def test_dry_run_saves_nothing(db, session_factory, jobs):
    totals = await run([ScriptedSource([jobs])], [LONDON], max_requests_per_day={"jsearch": 6},
                       session_factory=session_factory, dry_run=True)
    assert totals.fetched == 3 and totals.inserted == 0
    assert await count(db, Posting) == 0
    assert await count(db, CollectorRun) == 0