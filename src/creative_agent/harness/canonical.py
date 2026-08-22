"""Canonical scholarly identifiers: arXiv ids and DOIs.

Tool honesty matches on these, never raw URLs — redirects, /abs vs /pdf vs /vN variants,
and scheme noise must not defeat (or fake-satisfy) the verification cross-check. Both
identifier families therefore have to canonicalize from every form they appear in:
a URL, a prefixed citation string, or a bare id.
"""

from __future__ import annotations

import re

# Modern arXiv ids (2007+) are NNNN.NNNNN. Pre-2007 ids are archive/YYMMNNN with an
# optional subject class (math/0211159, cs.LG/0102030) — a corpus spanning both eras
# would otherwise leave older citations unmatchable by the tool-honesty check.
_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Za-z]{2})?/\d{7})"
_ARXIV = re.compile(
    r"(?:arxiv[.:]\s*(?:org/(?:abs|pdf)/)?|arxiv\.org/(?:abs|pdf)/)"
    rf"(?P<id>{_ARXIV_ID})(?:v\d+)?",
    re.IGNORECASE,
)
_ARXIV_BARE = re.compile(rf"\A(?P<id>{_ARXIV_ID})(?:v\d+)?\Z", re.IGNORECASE)

# DOIs legitimately contain parentheses (older Wiley ids such as
# 10.1002/(SICI)1097-0142(19960401)77:7). Excluding ')' from the character class made the
# URL form canonicalize to a truncated id while the bare form did not, so one work landed
# in two identity buckets. Trailing sentence punctuation is stripped after matching.
_DOI_BODY = r"10\.\d{4,9}/[^\s\"'<>]+"
_DOI = re.compile(rf"(?:doi\.org/|doi[:\s]+)(?P<id>{_DOI_BODY})", re.IGNORECASE)
_DOI_BARE = re.compile(rf"\A(?P<id>{_DOI_BODY})\Z", re.IGNORECASE)
_DOI_TRAILING = ".,;:"


def _clean_doi(raw: str) -> str:
    return raw.rstrip(_DOI_TRAILING).lower()


def canonicalize(text: str | None) -> str | None:
    """Extract a canonical identifier from a URL, citation string, or bare id."""
    if not text:
        return None
    candidate = text.strip()
    match = _ARXIV_BARE.match(candidate) or _ARXIV.search(candidate)
    if match:
        return f"arxiv:{match.group('id').lower()}"
    match = _DOI_BARE.match(candidate) or _DOI.search(candidate)
    if match:
        return f"doi:{_clean_doi(match.group('id'))}"
    return None


def all_identifiers(text: str) -> set[str]:
    """Every canonical identifier mentioned anywhere in a text."""
    found: set[str] = set()
    for match in _ARXIV.finditer(text):
        found.add(f"arxiv:{match.group('id').lower()}")
    for match in _DOI.finditer(text):
        found.add(f"doi:{_clean_doi(match.group('id'))}")
    return found
