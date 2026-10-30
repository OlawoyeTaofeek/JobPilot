"""save_facts, select_pending, run_batch and the batch lock against real Postgres.

No API calls: run_batch gets a fake extract_one (deterministic per posting), and save_facts
gets hand-made PostingFacts.
"""

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.collector.extract_pending as ep
from app.collector.extract import EXTRACTION_VERSION, ExtractionResult, save_facts
from app.collector.facts import LanguageRequirement, PostingFacts, SalaryFacts
from app.db.models import Posting, PostingSkill


def make_facts(**overrides) -> PostingFacts:
    data = dict(
        role_family="ml_engineer", seniority="mid", years_experience_min=2, work_mode="remote",
        remote_scope="worldwide", visa_sponsorship="yes", relocation_support="not_mentioned",
        clearance_required="not_mentioned",
        language_requirements=[LanguageRequirement(language="English", level="required"),
                               LanguageRequirement(language="German", level="preferred")],
        skills=["PyTorch", "Py-Torch", "AWS", "Communication"],
        salary=None, quality_flag="ok", confidence=0.9, evidence="fully remote",
    )
    data.update(overrides)
    return PostingFacts(**data)


async def add_posting(db, n: int, **fields) -> Posting:
    values = dict(source="test", source_job_id=str(n), fingerprint="f" * 64, title=f"Job {n}",
                  search_query="q", raw={}, description="This role is fully remote.")
    values.update(fields)
    posting = Posting(**values)
    db.add(posting)
    await db.flush()
    return posting


@pytest.fixture
def session_factory(db):
    """Every 'new' session is the rolled-back test session. The lock serialises the tasks
    that run_batch starts concurrently, because one AsyncSession can't be used by two at once."""
    lock = asyncio.Lock()

    @asynccontextmanager
    async def factory():
        async with lock:
            yield db
    return factory


@pytest.fixture
async def lock_engine():
    """A separate engine for the advisory lock (it needs its own connection)."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    yield engine
    await engine.dispose()


# ---- save_facts ---------------------------------------------------------------------

async def test_save_facts_writes_columns_and_normalised_skills(db):
    posting = await add_posting(db, 1)
    await save_facts(db, posting, ExtractionResult(posting.id, facts=make_facts()))

    assert posting.extraction_version == EXTRACTION_VERSION
    assert posting.extraction_error is None
    assert (posting.work_mode, posting.remote_scope, posting.visa_sponsorship) == ("remote", "worldwide", "yes")
    assert posting.required_languages == ["English"]
    assert posting.preferred_languages == ["German"]
    assert posting.extraction_confidence == pytest.approx(0.9)          # evidence is grounded
    skills = (await db.scalars(select(PostingSkill.skill).where(PostingSkill.posting_id == posting.id))).all()
    assert sorted(skills) == ["aws", "pytorch"]          # deduped, soft skill dropped


async def test_remote_scope_is_forced_for_non_remote_jobs(db):
    posting = await add_posting(db, 2)
    facts = make_facts(work_mode="hybrid", remote_scope="worldwide")      # the model slipped
    await save_facts(db, posting, ExtractionResult(posting.id, facts=facts))
    assert posting.remote_scope == "not_applicable"


async def test_ungrounded_evidence_caps_confidence(db):
    posting = await add_posting(db, 3)
    facts = make_facts(evidence="relocation to Berlin provided", confidence=0.95)
    await save_facts(db, posting, ExtractionResult(posting.id, facts=facts))
    assert posting.extraction_confidence == pytest.approx(0.3)


async def test_salary_only_fills_a_gap(db):
    with_salary = await add_posting(db, 4, salary_min=50000, salary_max=60000, salary_currency="GBP")
    without = await add_posting(db, 5)
    salary = SalaryFacts(min=1, max=2, currency="usd", period="year")
    for p in (with_salary, without):
        await save_facts(db, p, ExtractionResult(p.id, facts=make_facts(salary=salary)))

    assert (with_salary.salary_min, with_salary.salary_currency) == (50000, "GBP")   # untouched
    assert (without.salary_min, without.salary_max, without.salary_currency) == (1, 2, "USD")


async def test_failure_keeps_previous_facts(db):
    posting = await add_posting(db, 6)
    await save_facts(db, posting, ExtractionResult(posting.id, facts=make_facts()))
    await save_facts(db, posting, ExtractionResult(posting.id, error="TimeoutError: slow"))

    assert posting.extraction_error == "TimeoutError: slow"
    assert posting.work_mode == "remote"                 # the good facts survived
    assert posting.extracted_at is not None


async def test_reextraction_replaces_skills(db):
    posting = await add_posting(db, 7)
    await save_facts(db, posting, ExtractionResult(posting.id, facts=make_facts()))
    await save_facts(db, posting, ExtractionResult(posting.id, facts=make_facts(skills=["SQL"])))
    skills = (await db.scalars(select(PostingSkill.skill).where(PostingSkill.posting_id == posting.id))).all()
    assert skills == ["sql"]


# ---- select_pending -----------------------------------------------------------------

async def test_select_pending_flags(db):
    new = await add_posting(db, 10)
    old = await add_posting(db, 11, extracted_at=func.now(), extraction_version="v0")
    failed = await add_posting(db, 12, extracted_at=func.now(), extraction_version=EXTRACTION_VERSION,
                               extraction_error="boom")
    await add_posting(db, 13, extracted_at=func.now(), extraction_version=EXTRACTION_VERSION)   # done

    async def ids(**flags):
        return {p.id for p in await ep.select_pending(db, limit=50, **flags)}

    assert await ids() == {new.id}
    assert await ids(reextract_old=True) == {new.id, old.id}
    assert await ids(retry_failed=True) == {new.id, failed.id}


# ---- run_batch ----------------------------------------------------------------------

@pytest.fixture
def fake_extract(monkeypatch):
    """Postings titled 'BAD ...' fail; the rest succeed. Tracks the max number in flight."""
    state = {"in_flight": 0, "peak": 0}

    async def fake(agent, posting):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.01)
        state["in_flight"] -= 1
        if posting.title.startswith("BAD"):
            return ExtractionResult(posting.id, error="ValueError: boom")
        return ExtractionResult(posting.id, facts=make_facts(), input_tokens=1000, output_tokens=500)

    monkeypatch.setattr(ep, "extract_one", fake)
    return state


async def test_run_batch_extracts_all_pending_once(db, session_factory, lock_engine, fake_extract):
    for n in range(8):
        await add_posting(db, 100 + n, title="BAD job" if n == 3 else f"Job {n}")

    totals = await ep.run_batch(agent=object(), session_factory=session_factory, bind=lock_engine)
    assert (totals.selected, totals.saved, totals.failed) == (8, 7, 1)
    assert totals.input_tokens == 7000
    assert totals.cost_usd > 0
    assert fake_extract["peak"] <= 5                      # concurrency limit respected

    again = await ep.run_batch(agent=object(), session_factory=session_factory, bind=lock_engine)
    assert again.selected == 0                            # nothing is processed twice


async def test_run_batch_dry_run_saves_nothing(db, session_factory, lock_engine, fake_extract):
    posting = await add_posting(db, 200)
    totals = await ep.run_batch(dry_run=True, agent=object(), session_factory=session_factory,
                                bind=lock_engine)
    assert totals.saved == 1
    await db.refresh(posting)
    assert posting.extracted_at is None


async def test_run_batch_limit_is_capped(db, session_factory, lock_engine, fake_extract, monkeypatch):
    monkeypatch.setattr(ep.get_settings(), "extraction_max_postings_per_run", 2)
    for n in range(4):
        await add_posting(db, 300 + n)
    totals = await ep.run_batch(limit=100, agent=object(), session_factory=session_factory,
                                bind=lock_engine)
    assert totals.selected == 2


async def test_second_batch_skips_while_the_lock_is_held(db, session_factory, lock_engine, fake_extract):
    await add_posting(db, 400)
    async with ep.batch_lock(lock_engine) as got_it:
        assert got_it
        totals = await ep.run_batch(agent=object(), session_factory=session_factory, bind=lock_engine)
    assert totals.skipped_locked
    assert totals.selected == 0