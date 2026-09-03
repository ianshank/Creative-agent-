"""Canonical scholarly identifiers: arXiv ids and DOIs.

Tool honesty matches on these, never raw URLs — redirects, /abs vs /pdf vs /vN variants,
and scheme noise must not defeat (or fake-satisfy) the verification cross-check. Both
identifier families therefore have to canonicalize from every form they appear in:
a URL, a prefixed citation string, or a bare id.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

# Which hosts may *vouch* for an identifier (DEC-F12). Canonicalization is a substring
# match over an arbitrary string, which is right for identity bucketing but wrong as proof
# of retrieval: a fetch of `attacker.example/x?src=arxiv.org/abs/2401.12345` extracted the
# same identifier as a fetch of the paper. Only a fetch from the identifier's own registrar
# may credit it. Deliberately data, not literals — an institutional mirror or a DOI proxy
# is a settings change (`HarnessSettings.identifier_authority_hosts`), not a code change.
DEFAULT_IDENTIFIER_AUTHORITIES: Mapping[str, tuple[str, ...]] = {
    "arxiv": ("arxiv.org",),
    "doi": ("doi.org",),
}
# Schemes a fetch may be made over. A `file://` or `ftp://` target is never retrieval
# evidence for a scholarly identifier, whatever string it contains.
_EVIDENCE_SCHEMES = frozenset({"http", "https"})

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


def identifier_scheme(canonical_id: str | None) -> str | None:
    """The registry an identifier belongs to: 'arxiv:2401.12345' -> 'arxiv'."""
    if not canonical_id or ":" not in canonical_id:
        return None
    return canonical_id.split(":", 1)[0].lower()


def _host_is_under(host: str, authority: str) -> bool:
    """True for the authority itself or any subdomain of it.

    Suffix matching is anchored on a dot so `notarxiv.org` cannot pass as `arxiv.org`,
    while `export.arxiv.org` — the host the arXiv API actually serves from — does.
    """
    return host == authority or host.endswith(f".{authority}")


def fetched_identifier(
    target: str, authorities: Mapping[str, tuple[str, ...]] = DEFAULT_IDENTIFIER_AUTHORITIES
) -> str | None:
    """The identifier a fetch of `target` may legitimately vouch for, if any (DEC-F12).

    Returns None unless `target` is an http(s) URL whose host is an authority for the
    identifier the URL contains. That rules out both halves of the forgery this closes:

    - a decoy URL on any allowlisted host that merely mentions an identifier in its query
      or path (`attacker.example/x?src=arxiv.org/abs/2401.12345`), and
    - a local `Read` of a file whose *name* contains one (`refs/arxiv.org/abs/<id>.md`),
      which needs no network at all and which the artifact's own author controls, since the
      artifact repository is a read root.
    """
    try:
        parsed = urlparse(target)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _EVIDENCE_SCHEMES:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    # The host must be a registrar for *some* configured scheme, not specifically for the
    # scheme canonicalization happens to pick. Cross-registrar is deliberate and necessary:
    # arXiv's own DOI prefix is 10.48550, so `doi.org/10.48550/arXiv.<id>` — the standard
    # modern citation form — is a legitimate arXiv retrieval served by doi.org. Demanding
    # per-scheme agreement refused it and failed the review with exit 3.
    if not any(_host_is_under(host, a) for hosts in authorities.values() for a in hosts):
        return None
    # Only the host and path may carry the identifier. A registrar returns 200 for
    # arbitrary query strings, so `arxiv.org/?x=arxiv.org/abs/<id>` is the model choosing a
    # string, not evidence of what was served; matching anywhere in the URL let the decoy
    # simply move to the registrar host. The host is included because arXiv's own URLs
    # carry the identifier as `/abs/<id>` and need `arxiv.org` for context — and because
    # `urlparse` has already stripped the port, which otherwise defeated the match.
    return canonicalize(f"{host}{parsed.path}")
