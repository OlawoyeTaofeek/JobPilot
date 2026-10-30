"""The shared contract every job source implements.

Purpose
-------
`run_daily` only talks to this interface, so adding a source (JSearch, Tavily, LinkedIn via
treg, Greenhouse's API, ...) means writing one class; the pipeline does not change
(design decision D18: adapter pattern, dependency inversion).

What lives here
---------------
- `PlanItem`     one search to run: role, place, country, how recent.
- `FetchResult`  what any source returns from one fetch (raw jobs + cost/quota info).
- `Source`       the protocol: `name`, `async fetch(item)`, `normalize(raw, item)`.
- Shared errors  `SourceError`, `SourceAuthError`, `SourceQuotaError`.

Shared errors and the failure policy
------------------------------------
Each source translates its own SDK/HTTP errors into these three classes, so `run_daily`
can apply ONE policy to every source (design decision D23):

    SourceAuthError    bad or missing key        -> stop the whole run
    SourceQuotaError   out of credits / quota    -> stop this source for today
    SourceError        anything else (5xx, bad response, timeout) -> skip this search

`normalize` raises `ValueError` for a single unusable job; the pipeline skips and counts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.collector.normalize import PostingIn


# ---------------------------------------------------------------------------
# shared errors
# ---------------------------------------------------------------------------

class SourceError(Exception):
    """A source failed for this search. Default policy: skip this search, keep going."""

    quota_remaining: int | None = None  # filled in by sources that know it


class SourceAuthError(SourceError):
    """Bad, missing or forbidden credentials. Policy: nothing will work, stop the run."""


class SourceQuotaError(SourceError):
    """Out of credits or quota. Policy: stop this source for today, other sources go on."""


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

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
    provider: str                       # who we called: "rapidapi", "tavily", "anyapi"...
    query: str                          # the exact query text sent
    cost_usd: float | None = None       # paid providers (treg returns costUsd)
    quota_remaining: int | None = None  # requests left, if the provider reports it
    from_cache: bool | None = None      # some providers serve cached results


@runtime_checkable
class Source(Protocol):
    """Anything with these three members is a Source; no inheritance needed."""

    name: str

    async def fetch(self, item: PlanItem) -> FetchResult:
        """Fetch one page of raw jobs. Raises SourceAuthError / SourceQuotaError / SourceError."""
        ...

    def normalize(self, raw: dict, item: PlanItem) -> PostingIn:
        """One raw job -> PostingIn. Raises ValueError for jobs that can't be used."""
        ...