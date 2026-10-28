"""Normalizer (app/collector/normalize.py) using a real saved JSearch job."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.collector.normalize import PostingIn, clean_description, normalize_jsearch

# Real description from a UK posting: literal "\n" text mixed with real newlines.
UK_DESCRIPTION = (
    "Job Description\n\nGenAI / NLP Engineer (AWS Bedrock, RAG & Agentic AI)\n\n\\n\\n\n\n"
    "London | Hybrid (1-2 days onsite) | 8-10 Week Contract\n\n\\n\n\n"
    "• Designing and building end-to-end GenAI solutions   \n\n\\n\n\n"
    "• Developing production-grade RAG architectures\n\n\\n\\n\\n"
)


# ---- clean_description ----------------------------------------------------------

def test_clean_description_removes_literal_backslash_n():
    cleaned = clean_description(UK_DESCRIPTION)
    assert "\\n" not in cleaned


def test_clean_description_collapses_blank_lines_and_trailing_spaces():
    cleaned = clean_description(UK_DESCRIPTION)
    assert "\n\n\n" not in cleaned
    assert all(line == line.rstrip() for line in cleaned.split("\n"))
    assert cleaned == cleaned.strip()


def test_clean_description_keeps_the_content():
    cleaned = clean_description(UK_DESCRIPTION)
    assert cleaned.startswith("Job Description")
    assert "London | Hybrid (1-2 days onsite)" in cleaned
    assert "• Developing production-grade RAG architectures" in cleaned


@pytest.mark.parametrize("value", [None, "", "   \n\n  "])
def test_clean_description_empty_input(value):
    assert clean_description(value) == ""


def test_clean_description_windows_line_endings():
    assert clean_description("a\r\nb\r\n\r\n\r\n\r\nc") == "a\nb\n\nc"


# ---- normalize_jsearch: real job ------------------------------------------------

def test_normalize_real_job_maps_identity(jsearch_job_ng):
    p = normalize_jsearch(jsearch_job_ng, "AI engineer in Nigeria")
    assert isinstance(p, PostingIn)
    assert p.source == "jsearch"
    assert p.source_job_id == jsearch_job_ng["job_uid"]      # job_uid, NOT job_id
    assert p.source_job_id != jsearch_job_ng["job_id"]
    assert p.search_query == "AI engineer in Nigeria"
    assert p.raw == jsearch_job_ng                            # raw kept untouched


def test_normalize_real_job_maps_fields(jsearch_job_ng):
    p = normalize_jsearch(jsearch_job_ng, "q")
    assert p.title == "AI/ML Infrastructure Engineer"
    assert p.company == "Placements24"
    assert p.publisher == "WhatJobs"
    assert p.country == "NG"
    assert p.city == "Enugu"
    assert p.is_remote_source is False
    assert p.description and "\\n" not in p.description


def test_normalize_posted_at_is_timezone_aware_utc(jsearch_job_ng):
    p = normalize_jsearch(jsearch_job_ng, "q")
    assert p.posted_at is not None
    assert p.posted_at.utcoffset() == timedelta(0)
    assert p.posted_at.isoformat().startswith("2026-10-21T00:00:00")


def test_normalize_missing_salary_is_none(jsearch_job_ng):
    p = normalize_jsearch(jsearch_job_ng, "q")
    assert p.salary_min is None and p.salary_max is None and p.salary_currency is None


# ---- normalize_jsearch: messy data ----------------------------------------------

def test_salary_values_become_two_place_decimals(jsearch_job_ng):
    job = {**jsearch_job_ng, "job_min_salary": 90000, "job_max_salary": "120000.5",
           "job_salary_currency": "gbp", "job_salary_period": "YEAR"}
    p = normalize_jsearch(job, "q")
    assert p.salary_min == Decimal("90000.00")
    assert p.salary_max == Decimal("120000.50")
    assert p.salary_currency == "GBP"
    assert p.salary_period == "YEAR"


@pytest.mark.parametrize("bad", ["competitive", "nan", "", "N/A"])
def test_bad_salary_becomes_none_instead_of_failing(jsearch_job_ng, bad):
    p = normalize_jsearch({**jsearch_job_ng, "job_min_salary": bad}, "q")
    assert p.salary_min is None


@pytest.mark.parametrize("bad", ["yesterday", "", None, "2026-13-45"])
def test_bad_date_becomes_none(jsearch_job_ng, bad):
    p = normalize_jsearch({**jsearch_job_ng, "job_posted_at_datetime_utc": bad}, "q")
    assert p.posted_at is None


@pytest.mark.parametrize(("raw_country", "expected"), [("ng", "NG"), (" gb ", "GB"), ("NGA", None), ("", None), (None, None)])
def test_country_code_is_normalized(jsearch_job_ng, raw_country, expected):
    p = normalize_jsearch({**jsearch_job_ng, "job_country": raw_country}, "q")
    assert p.country == expected


def test_long_title_is_clipped_not_rejected(jsearch_job_ng):
    p = normalize_jsearch({**jsearch_job_ng, "job_title": "T" * 400}, "q")
    assert len(p.title) == 300


def test_blank_optional_fields_become_none(jsearch_job_ng):
    p = normalize_jsearch({**jsearch_job_ng, "employer_name": "   ", "job_city": ""}, "q")
    assert p.company is None and p.city is None


def test_non_bool_remote_flag_becomes_none(jsearch_job_ng):
    p = normalize_jsearch({**jsearch_job_ng, "job_is_remote": "yes"}, "q")
    assert p.is_remote_source is None


# ---- normalize_jsearch: required fields -----------------------------------------

@pytest.mark.parametrize(
    "missing",
    [{"job_uid": None}, {"job_uid": ""}, {"job_title": None}, {"job_title": "   "}],
)
def test_missing_uid_or_title_raises_value_error(jsearch_job_ng, missing):
    with pytest.raises(ValueError, match="job_uid or job_title"):
        normalize_jsearch({**jsearch_job_ng, **missing}, "q")


# ---- PostingIn itself -----------------------------------------------------------

def test_posting_in_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PostingIn(source="jsearch", source_job_id="x", title="t", search_query="q", raw={}, compnay="typo")


def test_posting_in_enforces_column_limits():
    with pytest.raises(ValidationError):
        PostingIn(source="jsearch", source_job_id="x", title="t" * 301, search_query="q", raw={})
    with pytest.raises(ValidationError):
        PostingIn(source="jsearch", source_job_id="x", title="t", search_query="q", raw={},
                  salary_min=Decimal("1.234"))
