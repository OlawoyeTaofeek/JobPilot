"""Integration fixtures: a REAL Postgres test database.

Each test runs inside a transaction that is rolled back afterwards, so tests
never see each other's data and the test database stays empty.

Needs the database from TEST_DATABASE_URL (default: jobagent_test on localhost:5432).
Create it once in pgAdmin or psql:  CREATE DATABASE jobagent_test;
If it can't be reached, these tests are skipped (unit tests still run).
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import DBAPIError

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture
async def db():
    """An AsyncSession on the test DB, rolled back after each test."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)   # idempotent: only creates missing tables
    except (OSError, ConnectionError, DBAPIError) as e:
        await engine.dispose()
        pytest.skip(
            f"test database not reachable ({e.__class__.__name__}). "
            "Start Postgres and create it: CREATE DATABASE jobagent_test;"
        )

    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()
    await engine.dispose()
