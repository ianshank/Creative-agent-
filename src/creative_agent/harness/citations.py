"""Citation resolution and oracle re-baselining.

Used only by `creative-agent oracles rebaseline` (review-time resolution was cut as
YAGNI in peer review): every SourceRef with an arXiv id is resolved against the arXiv
API and its author list diffed mechanically — the exact defect class (three fabricated
author lists in v1 of the sutton spec) this machinery exists to catch. Resolution
failure is data, not an error: unverified rows are severity-capped by the freshness rule.
"""

from __future__ import annotations

import unicodedata
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeET

from creative_agent.harness.protocols import Clock, ResolutionResult
from creative_agent.models.oracle import FreshnessMeta, OracleTable, SourceRef

_ATOM = "{http://www.w3.org/2005/Atom}"


def _fold_surname(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip().split()[-1])
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
        if not title or ("Error" in title and not authors):
            return ResolutionResult("unreachable", detail=f"arXiv error entry: {title!r}")
        declared = [_fold_surname(a) for a in ref.authors]
        resolved = [_fold_surname(a) for a in authors]
        if declared and declared != resolved:
            return ResolutionResult(
                "mismatch",
                resolved_authors=authors,
                resolved_title=title,
                detail=f"declared surnames {declared} != resolved {resolved}",
            )
        return ResolutionResult("resolved", resolved_authors=authors, resolved_title=title)


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
                else:
                    new_sources.append(source)
                    report.append(
                        f"{row.id}: {result.status} for {source.arxiv_id or source.citation!r}"
                        f" ({result.detail})"
                    )
            new_rows.append(row.model_copy(update={"sources": new_sources}))
        freshness = FreshnessMeta(
            last_rebaselined=today,
            rebaseline_count=table.freshness.rebaseline_count + 1,
            max_rebaselines_without_verification=(
                table.freshness.max_rebaselines_without_verification
            ),
        )
        updated = table.model_copy(update={"rows": new_rows, "freshness": freshness})
        return updated, report
