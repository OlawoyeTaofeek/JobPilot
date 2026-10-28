"""JobSource protocol: async fetch(query, location, page) -> list[RawJob].
Each source handles its own rate limit and attribution fields.

The shared contract every job source implements.

run_daily only talks to this interface, so adding a source (LinkedIn via treg,
Greenhouse, Adzuna, a SERP API...) means writing one class - nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.collector.normalize import PostingIn


@dataclass(frozen=True)
class PlanItem:
    """One search to run, e.g. ("machine learning engineer", "London", "gb")."""

    role: str
    place: str
    country: str               # ISO 3166-1 alpha-2, lowercase (e.g. "gb", not "uk")
    date_posted: str = "3days"


@dataclass
class FetchResult:
    """What any source returns from one fetch."""

    raw_jobs: list[dict]
    provider: str                       # who we called: "rapidapi", "anyapi", "direct"...
    query: str                          # the exact query text sent
    cost_usd: float | None = None       # paid providers (treg returns costUsd)
    quota_remaining: int | None = None  # requests left, if the provider reports it
    from_cache: bool | None = None      # some providers serve cached results


@runtime_checkable
class Source(Protocol):
    """Anything with these three members is a Source - no inheritance needed."""

    name: str

    async def fetch(self, item: PlanItem) -> FetchResult:
        """Fetch one page of raw jobs for this plan item. Raises the source's own errors."""
        ...

    def normalize(self, raw: dict, item: PlanItem) -> PostingIn:
        """One raw job -> PostingIn. Raises ValueError for jobs that can't be used."""
        ...