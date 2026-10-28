"""Posting table constraints against real Postgres."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.collector.normalize import normalize_jsearch
from app.db.models import Posting


def to_row(posting_in, fingerprint="f" * 64) -> Posting:
    return Posting(**posting_in.model_dump(), fingerprint=fingerprint)


async def test_normalized_real_job_can_be_saved(db, jsearch_job_ng):
    row = to_row(normalize_jsearch(jsearch_job_ng, "AI engineer in Nigeria"))
    db.add(row)
    await db.flush()
    await db.refresh(row)
    assert row.id is not None
    assert row.collected_at.tzinfo is not None                   # server default
    assert row.raw["job_uid"] == jsearch_job_ng["job_uid"]       # JSONB round-trip


async def test_same_source_job_twice_is_rejected(db, jsearch_job_ng):
    posting = normalize_jsearch(jsearch_job_ng, "q")
    db.add(to_row(posting))
    await db.flush()
    db.add(to_row(posting))
    with pytest.raises(IntegrityError):                          # uq(source, source_job_id)
        await db.flush()
    await db.rollback()


async def test_salary_range_check_constraint(db, jsearch_job_ng):
    posting = normalize_jsearch(jsearch_job_ng, "q")
    row = to_row(posting)
    row.salary_min, row.salary_max = Decimal("100"), Decimal("50")
    db.add(row)
    with pytest.raises(IntegrityError):                          # ck_postings_salary_range
        await db.flush()
    await db.rollback()
