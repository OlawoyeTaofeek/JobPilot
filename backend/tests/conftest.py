"""Shared test setup.

Environment variables are set BEFORE any `app` import, so settings, the JWT secret
and the database engine all point at test values - never at your dev database.
"""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent_test",
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
os.environ["JSEARCH_API_KEY"] = "test-jsearch-key"

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def jsearch_job_ng() -> dict:
    """One real JSearch job (Nigeria sample), loaded fresh for every test."""
    return json.loads((FIXTURES / "jsearch_job_ng.json").read_text(encoding="utf-8"))


@pytest.fixture
def jsearch_page_ng() -> dict:
    """A full /search-v2 response body with 3 real jobs and a cursor."""
    return json.loads((FIXTURES / "jsearch_page_ng.json").read_text(encoding="utf-8"))
