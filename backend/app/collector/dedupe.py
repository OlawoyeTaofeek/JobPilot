"""Cross-board dedupe: the same job on different boards gets the same fingerprint.

The fingerprint is indexed but NOT unique in the database: every row is kept,
and duplicates are grouped at query time. The unique key is (source, source_job_id).
"""

import hashlib
import re
import unicodedata

from app.collector.normalize import PostingIn

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SEPARATOR = "|"  # can't appear in normalized text, so fields never blur together


def normalize_text(value: str | None) -> str:
    """'  Zürich  AI/ML ' -> 'zurich ai ml'. None -> ''."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)              # 'ü' -> 'u' + accent mark
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = no_accents.lower()
    spaced = _NON_ALNUM.sub(" ", lowered)                          # punctuation -> space
    return " ".join(spaced.split())                                # collapse + strip


def fingerprint(posting: PostingIn) -> str:
    """sha256 of normalized company | title | place (city, else country). Always 64 hex chars."""
    company = normalize_text(posting.company)
    title = normalize_text(posting.title)
    place = normalize_text(posting.city or posting.country)
    key = _SEPARATOR.join([company, title, place])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()