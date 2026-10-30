"""Unit tests for the Tavily sources: URL patterns, title parsers, fetch, normalize, errors.

No network: a fake client stands in for AsyncTavilyClient.
When real runs show new page-title formats, add them to TITLE_CASES.
"""

import pytest
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    UsageLimitExceededError,
)

from app.collector.sources.base import (
    PlanItem,
    Source,
    SourceAuthError,
    SourceError,
    SourceQuotaError,
)
from app.collector.sources.tavily_search import (
    SITES,
    ats_source,
    boards_source,
    company_from_slug,
    parse_job_url,
)

LONDON = PlanItem("machine learning engineer", "London", "gb")


class FakeTavily:
    """Records search() calls; returns a canned response or raises."""

    def __init__(self, response=None, error=None):
        self.response = response or {"results": []}
        self.error = error
        self.calls = []

    async def search(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return self.response


# ---- URL patterns -------------------------------------------------------------------

@pytest.mark.parametrize("url, site, source_job_id", [
    ("https://boards.greenhouse.io/acme/jobs/4012345", "greenhouse", "acme/4012345"),
    ("https://job-boards.greenhouse.io/Acme-AI/jobs/123?gh_src=x", "greenhouse", "acme-ai/123"),
    ("https://job-boards.eu.greenhouse.io/acme/jobs/9", "greenhouse", "acme/9"),
    ("https://jobs.lever.co/mistral/0b7a1c2d-1111-2222-3333-444455556666", "lever",
     "mistral/0b7a1c2d-1111-2222-3333-444455556666"),
    ("https://jobs.ashbyhq.com/openai/0b7a1c2d-1111-2222-3333-444455556666/application", "ashby",
     "openai/0b7a1c2d-1111-2222-3333-444455556666"),
    ("https://apply.workable.com/huggingface/j/AB12CD34EF/", "workable", "huggingface/AB12CD34EF"),
    ("https://www.linkedin.com/jobs/view/ml-engineer-at-monzo-4012345678", "linkedin", "4012345678"),
    ("https://uk.linkedin.com/jobs/view/4012345678/", "linkedin", "4012345678"),
    ("https://uk.indeed.com/viewjob?jk=0123456789abcdef&from=serp", "indeed", "0123456789abcdef"),
    ("https://www.glassdoor.co.uk/job-listing/ml-acme-JV_IC1_KO0,2.htm?jl=1009123456", "glassdoor",
     "1009123456"),
])
def test_job_pages_are_recognised(url, site, source_job_id):
    ref = parse_job_url(url)
    assert ref is not None
    assert (ref.site.name, ref.source_job_id) == (site, source_job_id)


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme",                       # company board, not a job
    "https://jobs.lever.co/mistral",
    "https://www.linkedin.com/jobs/machine-learning-jobs",     # search page
    "https://uk.indeed.com/q-ml-jobs.html",
    "https://example.com/careers/123",
    "",
])
def test_non_job_pages_are_rejected(url):
    assert parse_job_url(url) is None


def test_parse_job_url_only_looks_at_the_given_sites():
    assert parse_job_url("https://boards.greenhouse.io/acme/jobs/1", ("linkedin",)) is None


# ---- page-title parsers ------------------------------------------------------------

TITLE_CASES = [
    ("greenhouse", "Job Application for Machine Learning Engineer at Acme AI",
     ("Machine Learning Engineer", "Acme AI", None)),
    ("lever", "Mistral AI - Applied AI Engineer, LLMs", ("Applied AI Engineer, LLMs", "Mistral AI", None)),
    ("ashby", "Senior ML Engineer @ OpenAI", ("Senior ML Engineer", "OpenAI", None)),
    ("workable", "Data Scientist - Hugging Face", ("Data Scientist", "Hugging Face", None)),
    ("linkedin", "Monzo hiring Machine Learning Engineer in London, England, United Kingdom | LinkedIn",
     ("Machine Learning Engineer", "Monzo", "London, England, United Kingdom")),
    ("glassdoor", "Revolut hiring Data Engineer Job in London, England | Glassdoor",
     ("Data Engineer", "Revolut", "London, England")),
    ("indeed", "Machine Learning Engineer - Ocado Group - Hatfield | Indeed.com",
     ("Machine Learning Engineer", "Ocado Group", "Hatfield")),
    ("indeed", "AI Engineer - London - Indeed.com", ("AI Engineer", None, None)),  # ambiguous: title only
]


@pytest.mark.parametrize("site, page_title, expected", TITLE_CASES)
def test_title_parsers(site, page_title, expected):
    parts = SITES[site].parse_title(page_title)
    assert (parts.title, parts.company, parts.location) == expected


@pytest.mark.parametrize("site", list(SITES))
def test_unknown_title_format_falls_back_to_the_raw_title(site):
    assert SITES[site].parse_title("Something completely different").title == "Something completely different"


def test_company_from_slug():
    assert company_from_slug("acme-ai") == "Acme Ai"
    assert company_from_slug("hugging_face") == "Hugging Face"


# ---- fetch --------------------------------------------------------------------------

RESPONSE = {"results": [
    {"url": "https://boards.greenhouse.io/acme-ai/jobs/4012345",
     "title": "Job Application for ML Engineer at Acme AI",
     "content": "snippet", "raw_content": "Full job description\n\n\n\nwith gaps"},
    {"url": "https://boards.greenhouse.io/acme-ai", "title": "Jobs at Acme", "content": "index page"},
    {"url": "https://www.linkedin.com/jobs/view/ml-eng-at-monzo-4012345678",
     "title": "Monzo hiring ML Engineer in London | LinkedIn",
     "content": "We are hiring", "raw_content": "page text we must not use"},
]}


def test_sources_follow_the_protocol():
    assert isinstance(ats_source(FakeTavily()), Source)
    assert isinstance(boards_source(FakeTavily()), Source)


async def test_ats_fetch_keeps_only_ats_job_pages_and_asks_for_page_text():
    client = FakeTavily(RESPONSE)
    result = await ats_source(client).fetch(LONDON)

    assert [j["url"] for j in result.raw_jobs] == ["https://boards.greenhouse.io/acme-ai/jobs/4012345"]
    assert result.provider == "tavily"
    params = client.calls[0]
    assert params["include_raw_content"] == "text"
    assert params["country"] == "united kingdom"
    assert params["time_range"] == "week"
    assert "boards.greenhouse.io" in params["include_domains"]
    assert "linkedin.com" not in params["include_domains"]


async def test_boards_fetch_never_asks_for_page_text():
    client = FakeTavily(RESPONSE)
    result = await boards_source(client).fetch(LONDON)

    assert [j["url"] for j in result.raw_jobs] == [
        "https://www.linkedin.com/jobs/view/ml-eng-at-monzo-4012345678"]
    assert client.calls[0]["include_raw_content"] is False


@pytest.mark.parametrize("error, expected", [
    (InvalidAPIKeyError("bad key"), SourceAuthError),
    (ForbiddenError("forbidden"), SourceAuthError),
    (UsageLimitExceededError("limit"), SourceQuotaError),
    (BadRequestError("bad request"), SourceError),
    (TimeoutError("slow"), SourceError),
])
async def test_tavily_errors_become_shared_source_errors(error, expected):
    with pytest.raises(expected):
        await ats_source(FakeTavily(error=error)).fetch(LONDON)


# ---- normalize ----------------------------------------------------------------------

def test_ats_normalize_builds_a_full_posting():
    posting = ats_source(FakeTavily()).normalize(RESPONSE["results"][0], LONDON)

    assert (posting.source, posting.source_job_id, posting.provider) == (
        "greenhouse", "acme-ai/4012345", "tavily")
    assert posting.title == "ML Engineer"
    assert posting.company == "Acme AI"
    assert posting.description == "Full job description\n\nwith gaps"   # cleaned page text
    assert posting.apply_url == "https://boards.greenhouse.io/acme-ai/jobs/4012345"
    assert posting.country == "GB"
    assert "raw_content" not in posting.raw                              # big text not stored twice


def test_boards_normalize_uses_the_snippet_not_the_page_text():
    posting = boards_source(FakeTavily()).normalize(RESPONSE["results"][2], LONDON)

    assert (posting.source, posting.source_job_id) == ("linkedin", "4012345678")
    assert posting.company == "Monzo"
    assert posting.location_text == "London"
    assert posting.description == "We are hiring"


def test_normalize_rejects_a_non_job_page():
    with pytest.raises(ValueError):
        ats_source(FakeTavily()).normalize(RESPONSE["results"][1], LONDON)


def test_company_falls_back_to_the_url_slug():
    raw = {"url": "https://jobs.lever.co/acme-ai/0b7a1c2d-1111-2222-3333-444455556666",
           "title": "Machine Learning Engineer", "content": "text"}   # no "Company - " prefix
    posting = ats_source(FakeTavily()).normalize(raw, LONDON)
    assert posting.company == "Acme Ai"