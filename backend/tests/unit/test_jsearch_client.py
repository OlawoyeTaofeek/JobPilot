"""JSearch client (app/collector/sources/jsearch.py) against a fake server.

httpx.MockTransport answers instead of RapidAPI, so these tests cost no quota.
"""

import httpx
import pytest

from app.collector.sources import jsearch
from app.collector.sources.jsearch import (
    BASE_URL,
    QUOTA_SAFETY_MARGIN,
    JSearchAuthError,
    JSearchClient,
    JSearchError,
    JSearchQuotaError,
    JSearchResponseError,
    JSearchUnavailableError,
    build_query,
)

QUERY = "AI engineer in London"


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    """Retries would sleep 2-8 s; skip the waiting in tests."""
    async def instant(_seconds):
        return None
    monkeypatch.setattr(jsearch.asyncio, "sleep", instant)


def ok(body: dict, quota: int = 150) -> httpx.Response:
    return httpx.Response(200, json=body, headers={"x-ratelimit-requests-remaining": str(quota)})


def make_client(*responses):
    """A JSearchClient whose requests get `responses` in order. Records each request."""
    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = JSearchClient("test-key")
    client._client = httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"x-rapidapi-key": "test-key", "x-rapidapi-host": jsearch.HOST},
        transport=httpx.MockTransport(handler),
    )
    return client, seen


# ---- build_query ----------------------------------------------------------------

def test_build_query_puts_place_in_text():
    assert build_query("AI engineer", "London") == "AI engineer in London"


def test_build_query_collapses_whitespace():
    assert build_query("  data   scientist ", " Dubai ") == "data scientist in Dubai"


@pytest.mark.parametrize(("role", "place"), [("", "London"), ("AI engineer", "   ")])
def test_build_query_rejects_empty(role, place):
    with pytest.raises(ValueError):
        build_query(role, place)


# ---- constructor ----------------------------------------------------------------

def test_missing_api_key_fails_fast():
    with pytest.raises(JSearchAuthError):
        JSearchClient("")


# ---- happy path -----------------------------------------------------------------

async def test_fetch_page_returns_jobs_cursor_and_quota(jsearch_page_ng):
    client, seen = make_client(ok(jsearch_page_ng, quota=150))
    async with client:
        page = await client.fetch_page(QUERY, "GB")
    assert len(page.jobs) == 3
    assert page.next_cursor == "NEXT"
    assert page.quota_remaining == 150

    request = seen[0]
    assert request.url.path == "/search-v2"
    assert request.url.params["query"] == QUERY
    assert request.url.params["country"] == "gb"          # normalized
    assert request.url.params["num_pages"] == "1"
    assert "cursor" not in request.url.params             # first page: no cursor
    assert request.headers["x-rapidapi-key"] == "test-key"


async def test_cursor_is_sent_when_given(jsearch_page_ng):
    client, seen = make_client(ok(jsearch_page_ng))
    async with client:
        await client.fetch_page(QUERY, "gb", cursor="NEXT")
    assert seen[0].url.params["cursor"] == "NEXT"


async def test_zero_jobs_is_a_valid_page_and_logs_warning(caplog):
    client, _ = make_client(ok({"data": {"jobs": [], "cursor": None}}))
    async with client:
        page = await client.fetch_page(QUERY, "gb")
    assert page.jobs == [] and page.next_cursor is None
    assert "0 jobs" in caplog.text


# ---- input validation -----------------------------------------------------------

async def test_unknown_country_raises_before_any_request():
    client, seen = make_client()
    async with client:
        with pytest.raises(ValueError, match="'uk'"):
            await client.fetch_page(QUERY, "uk")
    assert seen == []  # no quota spent


# ---- retries --------------------------------------------------------------------

async def test_429_then_success_is_retried(jsearch_page_ng):
    client, seen = make_client(httpx.Response(429), ok(jsearch_page_ng, quota=149))
    async with client:
        page = await client.fetch_page(QUERY, "gb")
    assert len(seen) == 2 and page.quota_remaining == 149


async def test_network_error_then_success_is_retried(jsearch_page_ng):
    client, seen = make_client(httpx.ConnectError("boom"), ok(jsearch_page_ng))
    async with client:
        page = await client.fetch_page(QUERY, "gb")
    assert len(seen) == 2 and len(page.jobs) == 3


async def test_repeated_429_raises_quota_error():
    client, seen = make_client(*[httpx.Response(429)] * 3)
    async with client:
        with pytest.raises(JSearchQuotaError):
            await client.fetch_page(QUERY, "gb")
    assert len(seen) == 3


async def test_repeated_5xx_raises_unavailable():
    client, _ = make_client(*[httpx.Response(503, text="down")] * 3)
    async with client:
        with pytest.raises(JSearchUnavailableError) as exc:
            await client.fetch_page(QUERY, "gb")
    assert exc.value.status_code == 503
    assert exc.value.body_snippet == "down"


async def test_repeated_network_errors_raise_unavailable_with_cause():
    client, _ = make_client(*[httpx.ConnectError("boom")] * 3)
    async with client:
        with pytest.raises(JSearchUnavailableError) as exc:
            await client.fetch_page(QUERY, "gb")
    assert isinstance(exc.value.__cause__, httpx.ConnectError)  # `from e` kept the cause


# ---- non-retryable errors -------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors_are_not_retried(status):
    client, seen = make_client(httpx.Response(status, text="You are not subscribed to this API."))
    async with client:
        with pytest.raises(JSearchAuthError) as exc:
            await client.fetch_page(QUERY, "gb")
    assert len(seen) == 1
    assert exc.value.status_code == status
    assert exc.value.query == QUERY and exc.value.country == "gb"


async def test_unexpected_status_raises_base_error():
    client, _ = make_client(httpx.Response(404, text="nope"))
    async with client:
        with pytest.raises(JSearchError) as exc:
            await client.fetch_page(QUERY, "gb")
    assert type(exc.value) is JSearchError and exc.value.status_code == 404


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"status": "OK"}),      # no "data"
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_bad_body_raises_response_error(response):
    client, _ = make_client(response)
    async with client:
        with pytest.raises(JSearchResponseError):
            await client.fetch_page(QUERY, "gb")


# ---- quota guard ----------------------------------------------------------------

async def test_quota_guard_blocks_next_request(jsearch_page_ng):
    low = QUOTA_SAFETY_MARGIN - 1
    client, seen = make_client(ok(jsearch_page_ng, quota=low))
    async with client:
        page = await client.fetch_page(QUERY, "gb")        # this one still returns
        assert page.quota_remaining == low
        with pytest.raises(JSearchQuotaError) as exc:
            await client.fetch_page(QUERY, "gb")           # this one is refused
    assert len(seen) == 1                                   # no request was sent
    assert exc.value.quota_remaining == low


# ---- error formatting -----------------------------------------------------------

def test_error_str_includes_context_and_caps_snippet():
    err = JSearchQuotaError(
        "Quota low", quota_remaining=7, status_code=200, query=QUERY,
        country="gb", body_snippet="x" * 1000,
    )
    text = str(err)
    assert "Quota low" in text and "status=200" in text and "quota_remaining=7" in text
    assert len(err.body_snippet) == 300
    assert isinstance(err, JSearchError)
