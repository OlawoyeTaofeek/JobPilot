"""Query helpers for postings."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.normalize import PostingIn
from app.db.models import Posting


async def upsert_many(db: AsyncSession, items: list[tuple[PostingIn, str]]) -> int:
    """Insert new postings; silently skip ones already stored (same source + source_job_id).

    items: (posting, fingerprint) pairs. Returns how many rows were actually inserted.
    Does NOT commit - the caller owns the transaction.
    """
    if not items:
        return 0

    rows = [{**posting.model_dump(), "fingerprint": fp} for posting, fp in items]
    stmt = (
        insert(Posting)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source", "source_job_id"])
        .returning(Posting.id)
    )
    result = await db.execute(stmt)
    return len(result.scalars().all())