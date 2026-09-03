"""Citation resolution and oracle re-baselining.

Used only by `creative-agent oracles rebaseline` (review-time resolution was cut as
YAGNI in peer review): every SourceRef with an arXiv id is resolved against the arXiv
API and its author list diffed mechanically — the exact defect class (three fabricated
author lists in v1 of the sutton spec) this machinery exists to catch. Resolution
failure is data, not an error: unverified rows are severity-capped by the freshness rule.
"""

from __future__ import annotations

import logging
import unicodedata
from urllib.parse import quote
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeET

from creative_agent.harness.logging import get_logger, log_event
from creative_agent.harness.protocols import CitationResolver, Clock, ResolutionResult
from creative_agent.models.oracle import FreshnessMeta, OracleTable, SourceRef

_ATOM = "{http://www.w3.org/2005/Atom}"
_LOG = get_logger(__name__)


def _fold_surname(name: str) -> str:
    """Last token, diacritic-folded. Blank names fold to "" rather than raising."""
    parts = name.strip().split()
    if not parts:
        return ""
    decomposed = unicodedata.normalize("NFKD", parts[-1])
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class ArxivCitationResolver:
    """Resolves arXiv-identified sources via the arXiv Atom API."""

    def __init__(self, api_url: str, timeout_seconds: float) -> None:
        self._api_url = api_url
        self._timeout = timeout_seconds

    async def resolve(self, ref: SourceRef) -> ResolutionResult:
        if not ref.arxiv_id:
            return ResolutionResult("skipped", detail="no arXiv id")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    self._api_url, params={"id_list": ref.arxiv_id, "max_results": 1}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ResolutionResult("unreachable", detail=str(exc))
        try:
            root: Element = SafeET.fromstring(response.text)
            entry = root.find(f"{_ATOM}entry")
        except Exception as exc:  # defusedxml raises several parse/defense errors
            return ResolutionResult("unreachable", detail=f"bad Atom feed: {exc}")
        if entry is None:
            return ResolutionResult("unreachable", detail="no entry in Atom feed")
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        authors = [
            name.strip()
            for author in entry.findall(f"{_ATOM}author")
            if (name := author.findtext(f"{_ATOM}name"))
        ]
        # arXiv reports lookup failures as a normal entry titled "Error" whose author is
        # the API itself — treat that as unreachable, never as an author mismatch, or a
        # typo'd id would be reported as the fabricated-citation defect class.
        entry_id = entry.findtext(f"{_ATOM}id") or ""
        if not title or "arxiv.org/api/errors" in entry_id or title.strip() == "Error":
            return ResolutionResult("unreachable", detail=f"arXiv error entry: {title!r}")
        declared = [folded for a in ref.authors if (folded := _fold_surname(a))]
        resolved = [folded for a in authors if (folded := _fold_surname(a))]
        if not declared:
            # Nothing to diff: an undeclared author list must not be laundered into a
            # verified source (that would lift the staleness cap for free).
            return ResolutionResult(
                "unreachable",
                resolved_authors=authors,
                resolved_title=title,
                detail="source declares no authors; cannot verify attribution",
            )
        if declared != resolved:
            return ResolutionResult(
                "mismatch",
                resolved_authors=authors,
                resolved_title=title,
                detail=f"declared surnames {declared} != resolved {resolved}",
            )
        return ResolutionResult("resolved", resolved_authors=authors, resolved_title=title)


class CrossrefCitationResolver:
    """Resolves DOI-identified sources via the Crossref REST API.

    Four of the shipped oracle's sources carry a DOI and no arXiv id, so no code path
    could ever verify them: `ArxivCitationResolver` returns `skipped` whenever `arxiv_id`
    is absent. Once the staleness cap actually fires (DEC-F13) that becomes a severity
    bug rather than a gap — a peer-reviewed paper's findings would be capped at Minor not
    because the evidence is weak but because nobody transcribed a resolvable identifier.

    Mirrors `ArxivCitationResolver`'s contract exactly, including its two hard-won
    refusals: an entry that declares no authors is `unreachable`, never auto-verified,
    and a transport or shape failure is `unreachable`, never `mismatch` — reporting a
    typo'd identifier as an author mismatch would accuse it of the fabricated-citation
    defect class.
    """

    def __init__(self, api_url: str, timeout_seconds: float, user_agent: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    async def resolve(self, ref: SourceRef) -> ResolutionResult:
        if not ref.doi:
            return ResolutionResult("skipped", detail="no DOI")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._api_url}/{quote(ref.doi, safe='')}",
                    headers={"User-Agent": self._user_agent, "Accept": "application/json"},
                    follow_redirects=True,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ResolutionResult("unreachable", detail=f"crossref request failed: {exc}")
        try:
            message = response.json()["message"]
        except (ValueError, KeyError, TypeError) as exc:
            return ResolutionResult("unreachable", detail=f"unexpected Crossref payload: {exc}")

        title = _first_title(message)
        authors = _crossref_authors(message)
        if not title:
            return ResolutionResult("unreachable", detail="Crossref record carries no title")
        declared = [folded for a in ref.authors if (folded := _fold_surname(a))]
        resolved = [folded for a in authors if (folded := _fold_surname(a))]
        if not declared:
            # Same refusal as the arXiv resolver: an undeclared author list must never be
            # laundered into a verified source, which would lift the staleness cap free.
            return ResolutionResult(
                "unreachable",
                resolved_authors=authors,
                resolved_title=title,
                detail="source declares no authors; cannot verify attribution",
            )
        if not resolved:
            return ResolutionResult(
                "unreachable",
                resolved_title=title,
                detail="Crossref record lists no authors to diff against",
            )
        if declared != resolved:
            return ResolutionResult(
                "mismatch",
                resolved_authors=authors,
                resolved_title=title,
                detail=f"declared surnames {declared} != resolved {resolved}",
            )
        return ResolutionResult("resolved", resolved_authors=authors, resolved_title=title)


def _first_title(message: dict[str, object]) -> str:
    titles = message.get("title")
    if isinstance(titles, list) and titles:
        return str(titles[0]).strip()
    if isinstance(titles, str):
        return titles.strip()
    return ""


def _crossref_authors(message: dict[str, object]) -> list[str]:
    """Crossref splits names into `given`/`family`; some records carry only `name`."""
    raw = message.get("author")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        given = str(entry.get("given", "")).strip()
        family = str(entry.get("family", "")).strip()
        whole = str(entry.get("name", "")).strip()
        full = f"{given} {family}".strip() or whole
        if full:
            names.append(full)
    return names


class CompositeCitationResolver:
    """Dispatches a SourceRef to whichever backend can identify it.

    Order matters only for a source carrying both identifiers, where the first backend
    that does not skip wins. `skipped` is the composite's answer only when *every*
    backend skipped, so a source with no resolvable identifier at all still reports
    honestly rather than borrowing another backend's failure.
    """

    def __init__(self, resolvers: list[CitationResolver]) -> None:
        self._resolvers = list(resolvers)

    async def resolve(self, ref: SourceRef) -> ResolutionResult:
        details: list[str] = []
        for resolver in self._resolvers:
            result = await resolver.resolve(ref)
            if result.status != "skipped":
                return result
            details.append(result.detail)
        return ResolutionResult(
            "skipped", detail="; ".join(d for d in details if d) or "no resolver applies"
        )


class NullCitationResolver:
    """Offline: marks everything skipped."""

    async def resolve(self, ref: SourceRef) -> ResolutionResult:
        return ResolutionResult("skipped", detail="offline")


class OracleRebaseliner:
    """Applies resolution results to an oracle table and bumps freshness metadata."""

    def __init__(self, resolver: object, clock: Clock) -> None:
        self._resolver = resolver
        self._clock = clock

    async def rebaseline(self, table: OracleTable) -> tuple[OracleTable, list[str]]:
        today = self._clock.now().date()
        report: list[str] = []
        new_rows = []
        for row in table.rows:
            new_sources: list[SourceRef] = []
            for source in row.sources:
                result: ResolutionResult = await self._resolver.resolve(source)  # type: ignore[attr-defined]
                if result.status == "resolved":
                    new_sources.append(
                        source.model_copy(update={"verified": True, "last_verified": today})
                    )
                    report.append(f"{row.id}: resolved {source.arxiv_id} ({result.resolved_title})")
                elif result.status == "mismatch":
                    # Clear last_verified too: a source whose author list no longer matches
                    # has no valid verification date, and leaving one would misreport the
                    # row as recently checked.
                    new_sources.append(
                        source.model_copy(update={"verified": False, "last_verified": None})
                    )
                    report.append(
                        f"{row.id}: AUTHOR MISMATCH on {source.arxiv_id} — {result.detail} "
                        "(the v1 defect class; fix the citation, do not soften)"
                    )
                    log_event(
                        _LOG,
                        logging.WARNING,
                        "rebaseline.author_mismatch",
                        row=row.id,
                        arxiv_id=source.arxiv_id,
                        detail=result.detail,
                    )
                else:
                    new_sources.append(source)
                    report.append(
                        f"{row.id}: {result.status} for {source.arxiv_id or source.citation!r}"
                        f" ({result.detail})"
                    )
            new_rows.append(row.model_copy(update={"sources": new_sources}))
            log_event(
                _LOG,
                logging.DEBUG,
                "rebaseline.row_done",
                row=row.id,
                sources=len(new_sources),
                verified=sum(1 for s in new_sources if s.verified),
            )
        freshness = FreshnessMeta(
            last_rebaselined=today,
            rebaseline_count=table.freshness.rebaseline_count + 1,
            max_rebaselines_without_verification=(
                table.freshness.max_rebaselines_without_verification
            ),
        )
        updated = table.model_copy(update={"rows": new_rows, "freshness": freshness})
        return updated, report
