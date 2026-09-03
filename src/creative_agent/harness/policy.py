"""Rules that more than one module must agree on (DEC-F25).

Every constant here was previously written twice. That is not a style problem: two
literals for one policy means loosening one and not the other, and each of these pairs
sits on a control path where the two halves must give the same answer or the control has
a hole in it.

- `EVIDENCE_SCHEMES` was `ALLOWED_FETCH_SCHEMES` in `security` and `_EVIDENCE_SCHEMES` in
  `canonical`. The first decides what the hook lets the model fetch; the second decides
  what counts as retrieval evidence for an identifier. Loosening one reopens
  `file://arxiv.org/...` on exactly one of the two paths — and which one is worse depends
  on which you loosened, which is the sort of question nobody should have to ask.
- `URL_PATTERN` was duplicated in `security` (which harvests the artifact's URLs into the
  fetch allowlist) and `sourcequality` (which judges the artifact's bibliography). If they
  disagree about what a URL is, a citation can be judged by one and unreachable by the
  other.
- `fold_name` was `_fold` in `verification` (the impersonation sweep) and `_fold_surname`
  in `citations` (the author-list diff). A name that folds one way for the sweep and
  another for the diff is a rebaseline that clears an author the sweep still flags.

This module deliberately holds no configuration. Everything here is an invariant: "only
http and https are retrieval evidence" is not a knob, and `HarnessSettings` is where knobs
live. The defect DEC-F25 fixes was that the invariants were written twice, not that they
were in code.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

# Schemes a fetch may use, and the only schemes whose results are retrieval evidence.
# A `file://` or `ftp://` target is never evidence for a scholarly identifier, whatever
# string it contains: `file://arxiv.org/etc/passwd` passed on every review before this
# was checked, and needed no cooperation from the artifact because `arxiv.org` is on
# every computed allowlist.
EVIDENCE_SCHEMES = frozenset({"http", "https"})

# What both the allowlist harvester and the bibliography checker call a URL. The trailing
# class excludes the characters that ordinarily terminate a URL in prose — quotes, angle
# brackets, and the closing halves of brackets a citation is wrapped in.
URL_PATTERN = re.compile(r"https?://[^\s\"'<>\])]+", re.IGNORECASE)


def host_of(url: str) -> str | None:
    """The lowercased hostname of `url`, or None when it has none or will not parse.

    Guarded, because `urlparse` raises. `URL_PATTERN` excludes `]` from its character
    class, so `http://[::1]/health` in an artifact matches as `http://[::1` — an unbalanced
    bracket that `urlparse` rejects with `ValueError("Invalid IPv6 URL")`. That fired in
    the *deterministic* source-quality sweep, so an ordinary sentence in a reviewed
    document ended an offline review with exit 5, "unexpected error" — the same
    misclassification DEC-F24 closed for a different module.

    This existed three times: guarded in `security` and `canonical`, unguarded in
    `sourcequality`. One definition is the fix, not a fourth `try/except` (DEC-F32).
    """
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def host_matches_suffix(host: str, suffix: str) -> bool:
    """True when `host` is `suffix` itself or a subdomain of it.

    Anchored on a dot so `notarxiv.org` cannot pass as `arxiv.org` while
    `export.arxiv.org` does, and tolerant of a leading dot in the suffix so `.internal`
    and `internal` mean the same thing. This was written three times with three different
    shapes — only one of which handled the leading dot — which is the drift this module
    exists to stop (DEC-F32).
    """
    normalized = suffix.lstrip(".").lower()
    candidate = host.lower()
    return bool(normalized) and (candidate == normalized or candidate.endswith(f".{normalized}"))


def fold_name(text: str) -> str:
    """NFKD-decompose, drop combining marks, casefold.

    So `Hernández-García` matches `Hernandez-Garcia`: the same person spelled two ways
    across a bibliography and a resolver response must compare equal, or the impersonation
    sweep flags an author the rebaseline just verified.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def fold_surname(name: str) -> str:
    """`fold_name` of the last whitespace-separated token; "" for a blank name.

    Author lists arrive as "Given Middle Family" from one source and "Family, Given" from
    another, so the last token is the only part that is reliably the same field. A blank
    name folds to "" rather than raising: a malformed entry in a resolver response is a
    diff result, not a crash.
    """
    parts = name.strip().split()
    return fold_name(parts[-1]) if parts else ""
