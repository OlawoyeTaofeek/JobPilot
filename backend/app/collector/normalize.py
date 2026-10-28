"""Raw source jobs -> PostingIn (one clean shape across all sources).

Pure functions: no HTTP, no database. Easy to unit-test with saved samples.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_TRIPLE_NEWLINES = re.compile(r"\n{3,}")
_CENTS = Decimal("0.01")


class PostingIn(BaseModel):
    """The one clean shape every source (JSearch, Greenhouse, Lever...) must produce.

    Mirrors the postings table minus what the DB / dedupe set
    (id, fingerprint, collected_at, created_at, updated_at).
    Field limits match the column sizes, so bad data fails here, not at INSERT.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # identity
    source: str = Field(max_length=30)
    source_job_id: str = Field(min_length=1, max_length=255)

    # what the job is
    title: str = Field(min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=255)
    company_website: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=100)
    description: str = ""
    apply_url: str | None = Field(default=None, max_length=2048)
    employment_type: str | None = Field(default=None, max_length=50)

    # where
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    location_text: str | None = Field(default=None, max_length=255)
    is_remote_source: bool | None = None

    # pay
    salary_min: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    salary_max: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    salary_currency: str | None = Field(default=None, max_length=3)
    salary_period: str | None = Field(default=None, max_length=20)

    # when / how we got it
    posted_at: datetime | None = None
    search_query: str = Field(max_length=255)
    raw: dict
    # optional extras some sources provide (e.g. LinkedIn via AnyAPI); extraction fills the rest
    provider: str | None = Field(default=None, max_length=30)
    external_apply_url: str | None = Field(default=None, max_length=2048)
    applicants: int | None = None
    work_mode: str | None = Field(default=None, max_length=20)
    seniority: str | None = Field(default=None, max_length=20)

# ---- helpers -------------------------------------------------------------------


def clean_description(text: str | None) -> str:
    """Tidy a job description: real newlines, no stray '\\n' text, no huge gaps."""
    if not text:
        return ""
    text = text.replace("\\n", "\n")                       # literal backslash-n -> newline
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # Windows / old Mac line endings
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _TRIPLE_NEWLINES.sub("\n\n", text)
    return text.strip()


def _to_decimal(value: Any) -> Decimal | None:
    """Salary -> Decimal with 2 places, or None if missing/invalid."""
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value)).quantize(_CENTS)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _parse_dt(value: str | None) -> datetime | None:
    """'2026-10-21T00:00:00.000Z' -> timezone-aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _clip(value: Any, limit: int) -> str | None:
    """Strip, empty -> None, and cut to the column length."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _upper_code(value: Any, length: int) -> str | None:
    """'ng' -> 'NG'; anything that isn't exactly `length` letters -> None."""
    code = str(value).strip().upper() if value else ""
    return code if len(code) == length and code.isalpha() else None


# ---- sources -------------------------------------------------------------------


def normalize_jsearch(raw: dict, search_query: str) -> PostingIn:
    """One raw JSearch job -> PostingIn. Raises ValueError if required fields are missing."""
    uid = _clip(raw.get("job_uid"), 255)
    title = _clip(raw.get("job_title"), 300)
    if not uid or not title:
        job_id = str(raw.get("job_id") or "?")[:40]
        raise ValueError(f"JSearch job missing job_uid or job_title (job_id={job_id})")

    is_remote = raw.get("job_is_remote")

    return PostingIn(
        source="jsearch",
        source_job_id=uid,
        title=title,
        company=_clip(raw.get("employer_name"), 255),
        company_website=_clip(raw.get("employer_website"), 500),
        publisher=_clip(raw.get("job_publisher"), 100),
        description=clean_description(raw.get("job_description")),
        apply_url=_clip(raw.get("job_apply_link"), 2048),
        employment_type=_clip(raw.get("job_employment_type"), 50),
        city=_clip(raw.get("job_city"), 120),
        state=_clip(raw.get("job_state"), 120),
        country=_upper_code(raw.get("job_country"), 2),
        location_text=_clip(raw.get("job_location"), 255),
        is_remote_source=is_remote if isinstance(is_remote, bool) else None,
        salary_min=_to_decimal(raw.get("job_min_salary")),
        salary_max=_to_decimal(raw.get("job_max_salary")),
        salary_currency=_upper_code(raw.get("job_salary_currency"), 3),
        salary_period=_clip(raw.get("job_salary_period"), 20),
        posted_at=_parse_dt(raw.get("job_posted_at_datetime_utc")),
        search_query=_clip(search_query, 255) or "",
        raw=raw,
    )