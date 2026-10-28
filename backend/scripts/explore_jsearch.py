"""JSearch (RapidAPI) source. Includes listings from Google for Jobs (LinkedIn, Indeed...).
"""
"""Explore the JSearch API: make ONE request, save the raw JSON, print a summary.

This is a throwaway learning script. Its job is to show you what the API really
returns BEFORE you design the Posting table. The real collector lives in
app/collector/sources/jsearch.py.

Run from backend/:
    uv run python -m scripts.explore_jsearch
    uv run python -m scripts.explore_jsearch --query "data scientist" --country ng
    uv run python -m scripts.explore_jsearch --query "AI engineer remote" --date-posted month

Each run uses 1 request from your monthly quota.
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

from app.config import get_settings

BASE_URL = "https://jsearch.p.rapidapi.com"
HOST = "jsearch.p.rapidapi.com"
SAMPLES_DIR = Path("data/samples")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Make one JSearch request and save the raw JSON.")
    p.add_argument("--query", default="machine learning engineer")
    p.add_argument("--country", default="ng", help="2-letter country code, e.g. ng, us, gb")
    p.add_argument("--date-posted", default="week", choices=["all", "today", "3days", "week", "month"])
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--endpoint", default="search-v2", help="as shown on your RapidAPI listing")
    return p.parse_args()


async def fetch(args: argparse.Namespace, api_key: str) -> httpx.Response:
    headers = {"x-rapidapi-key": api_key, "x-rapidapi-host": HOST}
    params = {
        "query": args.query,
        "page": args.page,
        "num_pages": 1,           # keep at 1: more pages can cost more quota
        "country": args.country,
        "date_posted": args.date_posted,
    }
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=30) as client:
        return await client.get(f"/{args.endpoint}", params=params)


def find_jobs(body: dict) -> list[dict]:
    """The job list is usually under 'data'. Handle both data=[...] and data={'jobs': [...]}."""
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("jobs", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def summarize(jobs: list[dict]) -> None:
    print(f"\nJobs returned: {len(jobs)}")
    if not jobs:
        return

    # Which fields exist, and how often are they empty? -> tells you what can be NOT NULL.
    fields = sorted({k for job in jobs for k in job})
    print(f"\nFields ({len(fields)}) and how often each is null/empty:")
    for f in fields:
        empty = sum(1 for j in jobs if j.get(f) in (None, "", [], {}))
        print(f"  {f:<40} empty in {empty:>2}/{len(jobs)}")

    # Where do the jobs originally come from? (LinkedIn, Indeed, company sites...)
    for source_field in ("job_publisher", "publisher", "source"):
        if any(source_field in j for j in jobs):
            counts = Counter(j.get(source_field) for j in jobs)
            print(f"\nOriginal source ({source_field}):")
            for name, n in counts.most_common():
                print(f"  {n:>3}  {name}")
            break

    print("\nFirst job (truncated):")
    first = {k: (v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v)
             for k, v in jobs[0].items()}
    print(json.dumps(first, indent=2, default=str)[:3000])


def save(body: dict, args: argparse.Namespace) -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{args.query}_{args.country}".lower()).strip("_")
    path = SAMPLES_DIR / f"jsearch_{slug}_{datetime.now():%Y%m%d_%H%M}.json"
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


async def main() -> int:
    args = parse_args()
    api_key = get_settings().jsearch_api_key
    if not api_key:
        print("JSEARCH_API_KEY is missing from .env", file=sys.stderr)
        return 1

    try:
        resp = await fetch(args, api_key)
    except httpx.HTTPError as e:
        print(f"Network error: {e!r}", file=sys.stderr)
        return 1

    print(f"HTTP {resp.status_code}  ({resp.request.url.copy_remove_param('query')})")
    quota = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}
    if quota:
        print("Quota headers:", quota)

    if resp.status_code in (401, 403):
        print("Rejected: check the key, and that you're SUBSCRIBED to JSearch on RapidAPI.", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        return 1
    if resp.status_code == 429:
        print("Rate limit / monthly quota reached.", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(resp.text[:1000], file=sys.stderr)
        return 1

    body = resp.json()
    path = save(body, args)
    print(f"Saved raw response -> {path}")
    print("Top-level keys:", list(body.keys()))
    summarize(find_jobs(body))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))