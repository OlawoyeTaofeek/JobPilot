"""Cross-board dedupe (app/collector/dedupe.py).

Goal: the SAME job posted on different boards gets the SAME fingerprint, while
genuinely different jobs (other title, company, city or seniority) do NOT.
"""

import pytest

from app.collector.dedupe import fingerprint, normalize_text
from app.collector.normalize import PostingIn, normalize_jsearch


def make_posting(**fields) -> PostingIn:
    """A minimal valid PostingIn; tests only set the fields they care about."""
    defaults = {"source": "jsearch", "source_job_id": "x", "title": "ML Engineer",
                "search_query": "q", "raw": {}}
    return PostingIn(**{**defaults, **fields})


# ---- normalize_text -------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AI/ML Engineer", "ai ml engineer"),         # punctuation -> space
        ("  Data   Scientist  ", "data scientist"),   # collapse + strip whitespace
        ("ZÜRICH", "zurich"),                         # accents removed, lowercased
        ("Société Générale", "societe generale"),
        ("C++ / Python-dev", "c python dev"),
        ("Placements24", "placements24"),             # digits kept
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize("empty", [None, "", "   ", "!!!", "—/—"])
def test_normalize_text_empty_or_punctuation_only_becomes_empty_string(empty):
    assert normalize_text(empty) == ""


def test_normalize_text_is_idempotent():
    once = normalize_text("  Zürich AI/ML  ")
    assert normalize_text(once) == once           # normalizing twice changes nothing


# ---- fingerprint: shape ---------------------------------------------------------

def test_fingerprint_is_64_lowercase_hex_chars():
    fp = fingerprint(make_posting(company="Acme", city="Lagos"))
    assert len(fp) == 64                          # fits the String(64) column
    assert fp == fp.lower()
    int(fp, 16)                                   # raises if not valid hex


def test_fingerprint_is_deterministic():
    posting = make_posting(company="Acme", city="Lagos")
    assert fingerprint(posting) == fingerprint(posting)


# ---- fingerprint: the SAME job across boards ------------------------------------

@pytest.mark.parametrize(
    ("first", "second"),
    [
        # case + punctuation + spacing differences (the Placements24 job from the sample)
        ({"title": "AI/ML Engineer", "company": "Placements24", "city": "Enugu"},
         {"title": "ai ml engineer", "company": "PLACEMENTS24 ", "city": "enugu"}),
        # accents in the city
        ({"title": "Data Scientist", "company": "Acme", "city": "Zürich"},
         {"title": "Data  Scientist", "company": "acme", "city": "Zurich"}),
        # punctuation in the company name
        ({"title": "ML Engineer", "company": "Acme, Inc.", "city": "London"},
         {"title": "ML Engineer", "company": "Acme Inc", "city": "London"}),
    ],
)
def test_same_job_on_different_boards_matches(first, second):
    assert fingerprint(make_posting(**first)) == fingerprint(make_posting(**second))


def test_fields_outside_the_key_do_not_affect_fingerprint():
    """Board, URL, description, salary... differ between boards for the same job."""
    a = make_posting(company="Acme", city="Lagos", source="jsearch", source_job_id="1",
                     publisher="LinkedIn", apply_url="https://a.example", description="short")
    b = make_posting(company="Acme", city="Lagos", source="greenhouse", source_job_id="2",
                     publisher="Indeed", apply_url="https://b.example", description="long " * 50)
    assert fingerprint(a) == fingerprint(b)


# ---- fingerprint: DIFFERENT jobs ------------------------------------------------

@pytest.mark.parametrize(
    "changed",
    [
        {"title": "Senior ML Engineer"},   # seniority matters: not stripped on purpose
        {"title": "Data Engineer"},
        {"company": "Globex"},
        {"city": "Abuja"},
    ],
)
def test_different_jobs_get_different_fingerprints(changed):
    base = {"title": "ML Engineer", "company": "Acme", "city": "Lagos"}
    assert fingerprint(make_posting(**base)) != fingerprint(make_posting(**{**base, **changed}))


def test_separator_keeps_fields_apart():
    """Without a separator, 'ab'+'c engineer' and 'a'+'bc engineer' would collide."""
    a = make_posting(company="ab", title="c engineer", city="Lagos")
    b = make_posting(company="a", title="bc engineer", city="Lagos")
    assert fingerprint(a) != fingerprint(b)


# ---- fingerprint: place fallback ------------------------------------------------

def test_country_is_used_when_city_is_missing():
    no_city = make_posting(company="Acme", country="NG")
    with_city = make_posting(company="Acme", country="NG", city="Lagos")
    assert fingerprint(no_city) != fingerprint(with_city)


def test_city_wins_over_country():
    """Same city, different country codes -> still the same job (city is used)."""
    a = make_posting(company="Acme", city="Lagos", country="NG")
    b = make_posting(company="Acme", city="Lagos", country=None)
    assert fingerprint(a) == fingerprint(b)


def test_missing_company_and_place_still_fingerprints():
    fp = fingerprint(make_posting(title="ML Engineer"))   # company, city, country all None
    assert len(fp) == 64


# ---- real data ------------------------------------------------------------------

def test_real_job_fingerprint_is_stable_across_searches(jsearch_job_ng):
    """The same job found by two different searches must fingerprint identically."""
    from_search_a = normalize_jsearch(jsearch_job_ng, "AI engineer in Nigeria")
    from_search_b = normalize_jsearch(jsearch_job_ng, "machine learning engineer in Enugu")
    assert fingerprint(from_search_a) == fingerprint(from_search_b)


def test_real_job_reposted_on_another_board_matches(jsearch_job_ng):
    original = normalize_jsearch(jsearch_job_ng, "q")
    elsewhere = normalize_jsearch(
        {**jsearch_job_ng,
         "job_uid": "different-uid==",
         "job_publisher": "LinkedIn",
         "job_title": jsearch_job_ng["job_title"].upper(),
         "employer_name": f"  {jsearch_job_ng['employer_name']}  "},
        "q",
    )
    assert original.source_job_id != elsewhere.source_job_id   # two rows in the table...
    assert fingerprint(original) == fingerprint(elsewhere)     # ...grouped as one job