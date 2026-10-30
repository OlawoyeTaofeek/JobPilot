"""JSearch (RapidAPI) source: fetch raw job postings. No cleaning, no DB."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from app.collector.normalize import PostingIn, normalize_jsearch
from .base import FetchResult, PlanItem
from app.collector.sources.base import (
    FetchResult, PlanItem, SourceAuthError, SourceError, SourceQuotaError,
)
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://jsearch.p.rapidapi.com"
HOST = "jsearch.p.rapidapi.com"
ENDPOINT = "/search-v2"
ALLOWED_COUNTRIES = frozenset(  # ISO 3166-1 alpha-2: Africa | UK/IE | N. America | Middle East | Europe
    "ng ke za  gb ie  us ca  ae qa sa  de nl fr es ch se dk fi no pl pt be at ee lu".split()
)
QUOTA_SAFETY_MARGIN = 10
MAX_ATTEMPTS = 3


class JSearchError(SourceError):
    """
    Base class for every JSearch failure. Carries context for logging.
    A failure the caller must handle (bad key, quota, unexpected response).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        query: str | None = None,
        country: str | None = None,
        body_snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.query = query
        self.country = country
        self.body_snippet = body_snippet[:300] if body_snippet else None

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.query:
            parts.append(f"query={self.query!r}")
        if self.country:
            parts.append(f"country={self.country}")
        return " | ".join(parts)


class JSearchAuthError(JSearchError, SourceAuthError):
    """401/403: bad key or not subscribed. Stop everything; fix .env."""


class JSearchQuotaError(JSearchError, SourceQuotaError):
    """Quota exhausted (429 after retries) or below the safety margin. Stop for today."""

    def __init__(
        self, message: str, *, quota_remaining: int | None = None, **kwargs
    ) -> None:
        super().__init__(message, **kwargs)
        self.quota_remaining = quota_remaining

    def __str__(self) -> str:
        base = super().__str__()
        if self.quota_remaining is None:
            return base
        return f"{base} | quota_remaining={self.quota_remaining}"


class JSearchUnavailableError(JSearchError):
    """Network error or 5xx after all retries. Skip this query, try the next."""


class JSearchResponseError(JSearchError):
    """200 but the body isn't what we expect (not JSON, no 'data'). The API may have changed."""


@dataclass
class JSearchPage:
    jobs: list[dict]
    next_cursor: str | None
    quota_remaining: int | None


def build_query(role: str, place: str) -> str:
    """Build a /search-v2 query with the location in the text, e.g. "AI engineer in London".

    /search-v2 can return nothing when the place is only sent as the `country` parameter,
    so the place always goes into the query string too.
    """
    role = " ".join(role.split())
    place = " ".join(place.split())
    if not role or not place:
        raise ValueError("role and place must not be empty")
    return f"{role} in {place}"


class JSearchClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise JSearchAuthError("JSEARCH_API_KEY is missing")
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30,
            headers={"x-rapidapi-key": api_key, "x-rapidapi-host": HOST},
        )
        self._last_quota: int | None = None

    async def __aenter__(self) -> "JSearchClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def fetch_page(
        self,
        query: str,
        country: str,
        date_posted: str = "3days",
        cursor: str | None = None,
    ) -> JSearchPage:
        """Fetch ONE page of results. Raises a JSearchError subclass on failure."""

        if self._last_quota is not None and self._last_quota < QUOTA_SAFETY_MARGIN:
            raise JSearchQuotaError(
                "Quota below safety margin; stopping before the next request",
                quota_remaining=self._last_quota,
                query=query,
                country=country,
            )

        country = country.strip().lower()
        if country not in ALLOWED_COUNTRIES:
            raise ValueError(
                f"Unsupported country {country!r}. Use an ISO code from ALLOWED_COUNTRIES (e.g. 'gb', not 'uk')."
            )

        params: dict[str, str | int] = {
            "query": query,
            "country": country,
            "date_posted": date_posted,
            "num_pages": 1,
        }
        if cursor is not None:
            params["cursor"] = cursor

        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                response = await self._client.get(ENDPOINT, params=params)
            except httpx.TransportError as e:
                if last:
                    raise JSearchUnavailableError(
                        f"Network error after {MAX_ATTEMPTS} attempts",
                        query=query,
                        country=country,
                    ) from e
                logger.warning("jsearch network error (attempt %d/%d): %r", attempt, MAX_ATTEMPTS, e)
                await asyncio.sleep(2**attempt)
                continue

            status = response.status_code

            if status in (401, 403):
                raise JSearchAuthError(
                    "Rejected: bad API key or not subscribed to JSearch",
                    status_code=status,
                    query=query,
                    country=country,
                    body_snippet=response.text,
                )

            if status == 429:
                if last:
                    raise JSearchQuotaError(
                        f"Rate limited (429) after {MAX_ATTEMPTS} attempts",
                        status_code=status,
                        query=query,
                        country=country,
                        body_snippet=response.text,
                        quota_remaining=_read_quota(response),
                    )
                logger.warning("jsearch 429 (attempt %d/%d), backing off", attempt, MAX_ATTEMPTS)
                await asyncio.sleep(2**attempt)
                continue

            if status >= 500:
                if last:
                    raise JSearchUnavailableError(
                        f"Server error after {MAX_ATTEMPTS} attempts",
                        status_code=status,
                        query=query,
                        country=country,
                        body_snippet=response.text,
                    )
                logger.warning("jsearch %d (attempt %d/%d), retrying", status, attempt, MAX_ATTEMPTS)
                await asyncio.sleep(2**attempt)
                continue

            if status != 200:
                raise JSearchError(
                    "Unexpected response status",
                    status_code=status,
                    query=query,
                    country=country,
                    body_snippet=response.text,
                )

            break  # 200 OK

        self._last_quota = _read_quota(response)

        try:
            data = response.json()["data"]
            jobs = data.get("jobs") or []
            next_cursor = data.get("cursor")
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise JSearchResponseError(
                "Response body is not in the expected format",
                status_code=response.status_code,
                query=query,
                country=country,
                body_snippet=response.text,
            ) from e
        
        logger.info(
            "jsearch query=%r country=%s jobs=%d next_page=%s quota=%s",
            query, country, len(jobs), next_cursor is not None, self._last_quota,
        )
        if not jobs:
            logger.warning("jsearch returned 0 jobs for query=%r country=%s", query, country)

        return JSearchPage(jobs=jobs, next_cursor=next_cursor, quota_remaining=self._last_quota)


def _read_quota(response: httpx.Response) -> int | None:
    """Monthly requests left, from RapidAPI's header (None if missing or not a number)."""
    value = response.headers.get("x-ratelimit-requests-remaining")
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None

class JSearchSource:
    """Wraps JSearchClient so run_daily can treat it like any other Source."""

    name = "jsearch"
    provider = "rapidapi"

    def __init__(self, client: JSearchClient) -> None:
        self._client = client

    async def fetch(self, item: PlanItem) -> FetchResult:
        query = build_query(item.role, item.place)
        page = await self._client.fetch_page(query, item.country, item.date_posted)
        return FetchResult(
            raw_jobs=page.jobs,
            provider=self.provider,
            query=query,
            quota_remaining=page.quota_remaining,
        )

    def normalize(self, raw: dict, item: PlanItem) -> PostingIn:
        posting = normalize_jsearch(raw, build_query(item.role, item.place))
        return posting.model_copy(update={"provider": self.provider})