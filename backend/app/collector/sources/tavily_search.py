"""Tavily sources: find jobs on company career pages (ATS) and on LinkedIn, Indeed, Glassdoor.

Purpose
-------
JSearch gives us licensed aggregator data. Tavily is a web-search API, which lets us find
job pages JSearch misses. This module turns Tavily search results into `PostingIn`s with
two Source instances built from the same class:

    ats_source(client)      name="ats"         Greenhouse, Lever, Ashby, Workable
    boards_source(client)   name="job_boards"  LinkedIn, Indeed, Glassdoor

    PlanItem("machine learning engineer", "London", "gb")
        -> ONE Tavily search restricted to that source's domains
        -> keep only URLs that are individual job pages (each site has a URL pattern)
        -> normalize(): job id from the URL, title/company/location from the page title,
           description from page text or snippet -> PostingIn

Why the two groups are treated differently
------------------------------------------
- ATS pages (employer-owned, public, made to be read and applied to): we request the page
  text (`include_raw_content`), so the extractor gets a full description.
- Job boards (LinkedIn, Indeed, Glassdoor): we use them as a SEARCH ENGINE would, keeping
  only the result's title, URL and snippet, and we do NOT pull their page content. Their
  terms forbid scraping, and full, licensed copies of these jobs already arrive through
  JSearch (and LinkedIn through treg/AnyAPI later). A board result is a discovery record:
  a link plus a short snippet. The cross-board fingerprint links it to the full copy when
  JSearch has the same job. (Design decisions D60 responsible data use, D10 fingerprint.)
  This is a policy flag (`include_page_text`), not hard-coded, so it is a visible choice.

Identity (design decisions D7, D8)
----------------------------------
- `posting.source`        = the site the job lives on: "greenhouse", "lever", "linkedin"...
- `posting.source_job_id` = the site's own id from the URL ("acme/4012345" for an ATS,
                            because ATS ids are only unique per company; the bare id for
                            boards, whose ids are global).
- `posting.provider`      = "tavily", the service we found it through.
- `Source.name`           = "ats" or "job_boards": used for budgets and `collector_runs`.

Errors (design decision D23)
----------------------------
Tavily SDK errors are translated into the shared errors from `base.py`:
invalid/missing key or forbidden -> SourceAuthError (stop the run); usage limit ->
SourceQuotaError (stop this source today); anything else -> SourceError (skip this search).

Known limitations
-----------------
- Search results are not a job database: freshness depends on Tavily's index, and a page
  may still be online after the role is filled. No reliable posted date.
- Titles, companies and locations come from page titles, whose formats can change. Every
  parser falls back to the raw title; collect real titles from runs and add them to tests.
- `country` is the search's country (a hint). The extractor reads the real location.
- Board records have only a snippet, so extraction on them is weak (often "too_short").
Next improvement: use Tavily only for DISCOVERY of ATS companies, then fetch their jobs from
the free public Greenhouse/Lever JSON APIs (structured location, dates, full text).

Cost
----
One basic search = 1 Tavily credit (advanced = 2); page text comes in the same call.
Each Source has its own daily budget (`tavily_max_requests_per_day`).

Related modules
---------------
- `app.collector.sources.base`  PlanItem, FetchResult, Source, shared errors
- `app.collector.normalize`     PostingIn, clean_description
- `app.collector.run_daily`     runs every Source through the same pipeline
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tavily import AsyncTavilyClient
from tavily.errors import (
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    TavilyKeylessLimitError,
    UsageLimitExceededError,
)

from app.collector.normalize import PostingIn, clean_description
from app.collector.sources.base import (
    FetchResult,
    PlanItem,
    SourceAuthError,
    SourceError,
    SourceQuotaError,
)

log = logging.getLogger(__name__)

PROVIDER = "tavily"
MAX_RESULTS = 20          # Tavily's maximum per search
SEARCH_DEPTH = "basic"    # 1 credit; "advanced" costs 2
MAX_DESCRIPTION_CHARS = 20_000

# PlanItem.date_posted (JSearch vocabulary) -> Tavily time_range
TIME_RANGE: dict[str, str | None] = {
    "today": "day", "3days": "week", "week": "week", "month": "month", "all": None,
}

# Tavily's `country` boosts results from that country (general topic only). It takes a
# country NAME. Codes not listed here are simply not sent. If Tavily ever rejects a name,
# remove it from this map.
COUNTRY_NAMES: dict[str, str] = {
    "ng": "nigeria", "ke": "kenya", "za": "south africa", "gb": "united kingdom",
    "ie": "ireland", "us": "united states", "ca": "canada", "ae": "united arab emirates",
    "qa": "qatar", "sa": "saudi arabia", "de": "germany", "nl": "netherlands",
    "fr": "france", "es": "spain", "ch": "switzerland", "se": "sweden", "dk": "denmark",
    "fi": "finland", "no": "norway", "pl": "poland", "pt": "portugal", "be": "belgium",
    "at": "austria", "ee": "estonia", "lu": "luxembourg",
}


# ---------------------------------------------------------------------------
# what we can read from a page title
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TitleParts:
    """Pieces recovered from a page title. Only `title` is guaranteed."""

    title: str
    company: str | None = None
    location: str | None = None


def _tidy(text: str | None) -> str | None:
    """Collapse whitespace; empty -> None."""
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip(" -|·")
    return text or None


def _strip_suffix(page_title: str, site_word: str) -> str:
    """Remove a trailing ' | LinkedIn', ' - Indeed.com', ' | Glassdoor' style suffix."""
    return re.sub(rf"\s*[|\--]\s*{site_word}(?:\.\w+)*\s*$", "", page_title, flags=re.I)


# --- ATS title formats -------------------------------------------------------

def _greenhouse_title(t: str) -> TitleParts:
    # "Job Application for Machine Learning Engineer at Acme"
    m = re.match(r"^\s*Job Application for (?P<title>.+?) at (?P<company>.+?)\s*$", t, re.I)
    if m:
        return TitleParts(_tidy(m["title"]) or t, _tidy(m["company"]))
    return TitleParts(_tidy(t) or t)


def _lever_title(t: str) -> TitleParts:
    # "Acme - Machine Learning Engineer"
    company, sep, title = t.partition(" - ")
    if sep and _tidy(title):
        return TitleParts(_tidy(title), _tidy(company))
    return TitleParts(_tidy(t) or t)


def _ashby_title(t: str) -> TitleParts:
    # "Machine Learning Engineer @ Acme"
    title, sep, company = t.rpartition(" @ ")
    if sep and _tidy(title):
        return TitleParts(_tidy(title), _tidy(company))
    return TitleParts(_tidy(t) or t)


def _workable_title(t: str) -> TitleParts:
    # "Machine Learning Engineer - Acme"
    title, sep, company = t.rpartition(" - ")
    if sep and _tidy(title):
        return TitleParts(_tidy(title), _tidy(company))
    return TitleParts(_tidy(t) or t)


# --- job-board title formats -------------------------------------------------

def _linkedin_title(t: str) -> TitleParts:
    # "Acme hiring Machine Learning Engineer in London, England, United Kingdom | LinkedIn"
    t = _strip_suffix(t, "LinkedIn")
    m = re.match(r"^(?P<company>.+?) hiring (?P<title>.+?)(?: in (?P<location>.+))?$", t)
    if m:
        return TitleParts(_tidy(m["title"]) or t, _tidy(m["company"]), _tidy(m["location"]))
    return TitleParts(_tidy(t) or t)


def _glassdoor_title(t: str) -> TitleParts:
    # "Acme hiring Machine Learning Engineer Job in London, England | Glassdoor"
    t = _strip_suffix(t, "Glassdoor")
    m = re.match(r"^(?P<company>.+?) hiring (?P<title>.+?)(?: Job)?(?: in (?P<location>.+))?$", t)
    if m:
        return TitleParts(_tidy(m["title"]) or t, _tidy(m["company"]), _tidy(m["location"]))
    return TitleParts(_tidy(t) or t)


def _indeed_title(t: str) -> TitleParts:
    # "Machine Learning Engineer - Acme - London | Indeed.com"  (title - company - location)
    t = _strip_suffix(t, "Indeed")
    parts = [p for p in (_tidy(x) for x in t.split(" - ")) if p]
    if len(parts) >= 3:
        return TitleParts(parts[0], parts[1], " - ".join(parts[2:]))
    if parts:
        return TitleParts(parts[0])  # 2 parts is ambiguous (company or location?): title only
    return TitleParts(t)


# ---------------------------------------------------------------------------
# the sites we know
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Site:
    """Everything we need to know about one website."""

    name: str                                 # becomes posting.source
    domains: tuple[str, ...]                  # for Tavily's include_domains
    job_url: re.Pattern[str]                  # matches INDIVIDUAL job pages only
    parse_title: Callable[[str], TitleParts]
    ids_are_per_company: bool = False         # ATS ids repeat across companies


SITES: dict[str, Site] = {s.name: s for s in [
    # --- ATS: employer career pages -----------------------------------------
    Site(
        "greenhouse",
        ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"),
        re.compile(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/"
                   r"(?P<company>[\w-]+)/jobs/(?P<job_id>\d+)", re.I),
        _greenhouse_title, ids_are_per_company=True,
    ),
    Site(
        "lever",
        ("jobs.lever.co", "jobs.eu.lever.co"),
        re.compile(r"^https?://jobs(?:\.eu)?\.lever\.co/"
                   r"(?P<company>[\w.-]+)/(?P<job_id>[0-9a-f-]{36})", re.I),
        _lever_title, ids_are_per_company=True,
    ),
    Site(
        "ashby",
        ("jobs.ashbyhq.com",),
        re.compile(r"^https?://jobs\.ashbyhq\.com/"
                   r"(?P<company>[\w.%-]+)/(?P<job_id>[0-9a-f-]{36})", re.I),
        _ashby_title, ids_are_per_company=True,
    ),
    Site(
        "workable",
        ("apply.workable.com",),
        re.compile(r"^https?://apply\.workable\.com/"
                   r"(?P<company>[\w-]+)/j/(?P<job_id>[0-9A-Z]+)", re.I),
        _workable_title, ids_are_per_company=True,
    ),
    # --- job boards: discovery only (no page content) ----------------------
    Site(
        "linkedin",
        ("linkedin.com",),
        # www.linkedin.com/jobs/view/ml-engineer-at-acme-4012345678  or  /jobs/view/4012345678
        re.compile(r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/jobs/view/"
                   r"(?:[\w%-]*?-)?(?P<job_id>\d{8,})", re.I),
        _linkedin_title,
    ),
    Site(
        "indeed",
        ("indeed.com",),
        # uk.indeed.com/viewjob?jk=0123456789abcdef  (also /rc/clk?jk=..., /m/viewjob?jk=...)
        re.compile(r"^https?://(?:[a-z]{2,3}\.|www\.)?indeed\.com/.*?[?&]jk=(?P<job_id>[0-9a-f]{16})",
                   re.I),
        _indeed_title,
    ),
    Site(
        "glassdoor",
        ("glassdoor.com", "glassdoor.co.uk", "glassdoor.ca", "glassdoor.de", "glassdoor.ie",
         "glassdoor.fr", "glassdoor.nl", "glassdoor.es"),
        # www.glassdoor.co.uk/job-listing/ml-engineer-acme-JV_IC123_KO0,11_KE12,16.htm?jl=1009123456
        re.compile(r"^https?://(?:www\.)?glassdoor\.[a-z.]+/job-listing/.*?[?&]jl=(?P<job_id>\d+)",
                   re.I),
        _glassdoor_title,
    ),
]}

ATS_SITES = ("greenhouse", "lever", "ashby", "workable")
BOARD_SITES = ("linkedin", "indeed", "glassdoor")


@dataclass(frozen=True)
class JobRef:
    """What a job URL tells us: which site, which job (and, for an ATS, which company)."""

    site: Site
    job_id: str
    company_slug: str | None = None

    @property
    def source_job_id(self) -> str:
        """Unique within the site: ATS ids need the company in front."""
        if self.site.ids_are_per_company and self.company_slug:
            return f"{self.company_slug}/{self.job_id}"
        return self.job_id


def parse_job_url(url: str, sites: tuple[str, ...] = tuple(SITES)) -> JobRef | None:
    """JobRef for an individual job page on one of `sites`, else None."""
    for name in sites:
        site = SITES[name]
        match = site.job_url.match(url or "")
        if match:
            slug = match.groupdict().get("company")
            return JobRef(site=site, job_id=match["job_id"],
                          company_slug=slug.lower() if slug else None)
    return None


def company_from_slug(slug: str) -> str:
    """'acme-ai' -> 'Acme Ai'. Fallback when the page title doesn't name the company."""
    words = re.sub(r"[-_.]|%20", " ", slug)
    return re.sub(r"\s+", " ", words).strip().title()

class TavilySource:
    """Finds individual job pages on a group of sites with Tavily (Source protocol)."""

    provider = PROVIDER

    def __init__(
        self,
        client: AsyncTavilyClient,
        *,
        name: str,
        sites: tuple[str, ...],
        include_page_text: bool,
    ) -> None:
        # The client is passed in (not created at import time): importing needs no key,
        # and tests can pass a fake client.
        self._client = client
        self.name = name
        self.sites = sites
        self.include_page_text = include_page_text
        self._domains = [d for s in sites for d in SITES[s].domains]

    @staticmethod
    def build_query(item: PlanItem) -> str:
        """Search text, written like a person would type it."""
        return f"{item.role} jobs in {item.place}"

    async def fetch(self, item: PlanItem) -> FetchResult:
        """One Tavily search restricted to this source's sites; keep individual job pages."""
        query = self.build_query(item)
        params: dict[str, Any] = {
            "query": query,
            "search_depth": SEARCH_DEPTH,
            "max_results": MAX_RESULTS,
            "include_domains": self._domains,
            "include_raw_content": "text" if self.include_page_text else False,
        }
        time_range = TIME_RANGE.get(item.date_posted, "week")
        if time_range:
            params["time_range"] = time_range
        country = COUNTRY_NAMES.get(item.country.lower())
        if country:
            params["country"] = country

        try:
            response = await self._client.search(**params)
        except (InvalidAPIKeyError, MissingAPIKeyError, ForbiddenError) as e:
            raise SourceAuthError(f"tavily: {type(e).__name__}: {e}") from e
        except (UsageLimitExceededError, TavilyKeylessLimitError) as e:
            raise SourceQuotaError(f"tavily: {type(e).__name__}: {e}") from e
        except Exception as e:  # network, timeout, bad request, unexpected response
            raise SourceError(f"tavily: {type(e).__name__}: {e} | query={query!r}") from e

        results = response.get("results") or []
        jobs = [r for r in results if parse_job_url(r.get("url", ""), self.sites)]
        log.info("%s: %r -> %d results, %d job pages (%d dropped)",
                 self.name, query, len(results), len(jobs), len(results) - len(jobs))
        return FetchResult(raw_jobs=jobs, provider=PROVIDER, query=query)

    def normalize(self, raw: dict[str, Any], item: PlanItem) -> PostingIn:
        """One Tavily result -> PostingIn. Raises ValueError if it isn't a usable job page."""
        url = raw.get("url") or ""
        ref = parse_job_url(url, self.sites)
        if ref is None:
            raise ValueError(f"not a job page: {url!r}")

        parts = ref.site.parse_title(raw.get("title") or "")
        title = _tidy(parts.title)
        if not title:
            raise ValueError(f"no title for {url!r}")

        company = parts.company or (company_from_slug(ref.company_slug) if ref.company_slug else None)
        text = raw.get("raw_content") if self.include_page_text else None
        description = clean_description(text or raw.get("content") or "")[:MAX_DESCRIPTION_CHARS]

        # Keep the search result itself, minus the big page text (already in `description`).
        stored = {k: v for k, v in raw.items() if k != "raw_content"}

        return PostingIn(
            source=ref.site.name,
            source_job_id=ref.source_job_id,
            provider=PROVIDER,
            title=title[:300],
            company=company[:255] if company else None,
            description=description,
            apply_url=url,
            location_text=parts.location[:255] if parts.location else None,
            country=item.country.upper(),       # a HINT from the search, not a fact
            search_query=self.build_query(item)[:255],
            raw=stored,
        )

def ats_source(client: AsyncTavilyClient) -> TavilySource:
    """Employer career pages (Greenhouse, Lever, Ashby, Workable), with full page text."""
    return TavilySource(client, name="ats", sites=ATS_SITES, include_page_text=True)

def boards_source(client: AsyncTavilyClient) -> TavilySource:
    """LinkedIn, Indeed, Glassdoor as discovery only: title, link and snippet, no page text."""
    return TavilySource(client, name="job_boards", sites=BOARD_SITES, include_page_text=False)