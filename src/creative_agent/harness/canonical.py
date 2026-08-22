"""Canonical scholarly identifiers: arXiv ids and DOIs.

Tool honesty matches on these, never raw URLs — redirects, /abs vs /pdf vs /vN variants,
and scheme noise must not defeat (or fake-satisfy) the verification cross-check.
"""

from __future__ import annotations

import re

_ARXIV = re.compile(
    r"(?:arxiv[.:]\s*(?:org/(?:abs|pdf)/)?|arxiv\.org/(?:abs|pdf)/)"
    r"(?P<id>\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
_ARXIV_BARE = re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?:v\d+)?$")
_DOI = re.compile(r"(?:doi\.org/|doi[:\s]+)(?P<id>10\.\d{4,9}/[^\s\"'<>\]),]+)", re.IGNORECASE)
_DOI_BARE = re.compile(r"^(?P<id>10\.\d{4,9}/\S+)$")


def canonicalize(text: str | None) -> str | None:
    """Extract a canonical identifier from a URL, citation string, or bare id."""
    if not text:
        return None
    candidate = text.strip()
    match = _ARXIV_BARE.match(candidate) or _ARXIV.search(candidate)
    if match:
        return f"arxiv:{match.group('id')}"
    match = _DOI_BARE.match(candidate) or _DOI.search(candidate)
    if match:
        return f"doi:{match.group('id').rstrip('.').lower()}"
    return None


def all_identifiers(text: str) -> set[str]:
    """Every canonical identifier mentioned anywhere in a text."""
    found: set[str] = set()
    for match in _ARXIV.finditer(text):
        found.add(f"arxiv:{match.group('id')}")
    for match in _DOI.finditer(text):
        found.add(f"doi:{match.group('id').rstrip('.').lower()}")
    return found
