"""Integration fixtures: a REAL Postgres test database.

Schema
------
At the start of each test session the test schema is rebuilt from today's models
(`drop_all` then `create_all`, once). `create_all` alone only creates MISSING tables and
never alters existing ones, so after a model change (new columns) an old test table would
stay out of date and tests would fail with "column ... does not exist". Rebuilding keeps
the test schema identical to the code.

Because of `drop_all`, the URL MUST point at a throwaway database: the fixture refuses to
run unless the database name contains "test".

Isolation
---------
Each test runs inside a transaction that is rolled back afterwards
(`join_transaction_mode="create_savepoint"`), so tests never see each other's data, even
when the code under test calls `commit()`.

Setup
-----
Needs the database from TEST_DATABASE_URL (default: jobagent_test on localhost:5432).
Create it once in pgAdmin or psql:  CREATE DATABASE jobagent_test;
If it can't be reached, these tests are skipped (unit tests still run).
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]

# Rebuild the schema only once per test session (not before every test: that would be slow).
_schema_ready = False


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def _assert_is_test_database(url: str) -> None:
    """drop_all deletes everything: never let it touch a real database."""
    name = make_url(url).database or ""
    if "test" not in name:
        raise RuntimeError(
            f"TEST_DATABASE_URL points at database {name!r}. Integration tests drop and "
            "recreate all tables, so the name must contain 'test' (e.g. jobagent_test)."
        )


@pytest_asyncio.fixture
async def db():
    """An AsyncSession on the test DB, rolled back after each test."""
    global _schema_ready
    _assert_is_test_database(TEST_DATABASE_URL)

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        if not _schema_ready:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)    # throw away old tables
                await conn.run_sync(Base.metadata.create_all)  # build them from today's models
            _schema_ready = True
        else:
            async with engine.connect():                       # just check it's reachable
                pass
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