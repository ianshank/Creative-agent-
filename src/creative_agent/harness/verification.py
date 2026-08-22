"""VerificationLogChecker — the hard rules, enforced mechanically.

An incomplete verification log fails the review (never softened); tool honesty is
checked against observed tool *results*, matched on canonical identifiers so URL noise
neither defeats nor fake-satisfies the check; impersonation/attribution phrasing for
oracle-source authors is swept deterministically.
"""

from __future__ import annotations

import re
import unicodedata

from creative_agent.harness.canonical import canonicalize
from creative_agent.harness.llm.base import ToolEvidence
from creative_agent.models.findings import Finding
from creative_agent.models.verification import VerificationEntry

# Tools whose successful results count as "fetched" evidence. WebSearch is deliberately
# absent: search snippets support existence, never content (spec tool-honesty rule).
DEFAULT_FETCH_TOOLS = frozenset({"WebFetch", "Read"})

_IMPERSONATION_TEMPLATES = (
    r"\b{name}\s+would\s+(?:say|argue|call|reject)",
    r"\b{name}\s+believes?\b",
    r"\baccording\s+to\s+{name}\b(?![^.]{{0,80}}\((?:19|20)\d\d\))",
)


def _fold(text: str) -> str:
    """NFKD-fold + casefold so 'Hernández-García' matches 'Hernandez-Garcia'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class VerificationLogChecker:
    """Returns a defect list; an empty list is the only pass."""

    def __init__(
        self,
        author_names: set[str],
        fetch_tools: frozenset[str] = DEFAULT_FETCH_TOOLS,
    ) -> None:
        self._surnames = sorted({name.split()[-1] for name in author_names if name.strip()})
        self._fetch_tools = fetch_tools

    def _evidence_identifiers(self, evidence: list[ToolEvidence]) -> set[str]:
        identifiers: set[str] = set()
        for item in evidence:
            if item.ok and item.tool_name in self._fetch_tools:
                canonical = canonicalize(item.target)
                if canonical:
                    identifiers.add(canonical)
                identifiers.add(item.target)
        return identifiers

    def check_completeness(
        self, findings: list[Finding], entries: list[VerificationEntry]
    ) -> list[str]:
        """Every doctrinal finding needs a verification entry for each row it cites."""
        defects: list[str] = []
        entry_rows = {e.row_id for e in entries if e.row_id}
        for finding in findings:
            for row_id in finding.doctrine_refs:
                if row_id not in entry_rows:
                    defects.append(
                        f"finding {finding.finding_id} cites {row_id} but the "
                        f"verification log has no entry for {row_id}"
                    )
        return defects

    def check_tool_honesty(
        self, entries: list[VerificationEntry], evidence: list[ToolEvidence]
    ) -> list[str]:
        """fetched=True must be backed by a successful fetch-class tool result."""
        observed = self._evidence_identifiers(evidence)
        defects: list[str] = []
        for index, entry in enumerate(entries):
            if not entry.fetched:
                continue
            claimed = {
                identifier
                for identifier in (
                    canonicalize(entry.canonical_id),
                    canonicalize(entry.source_url),
                    entry.source_url,
                )
                if identifier
            }
            if not claimed & observed:
                defects.append(
                    f"verification entry {index} claims fetched=True for "
                    f"{entry.source_url or entry.canonical_id!r} but no successful "
                    "fetch-class tool result matches it"
                )
        return defects

    def check_attribution(self, prose_blocks: list[str]) -> list[str]:
        """No invented positions: impersonation phrasing for oracle authors is a defect."""
        defects: list[str] = []
        for block in prose_blocks:
            folded = _fold(block)
            for surname in self._surnames:
                folded_surname = re.escape(_fold(surname))
                for template in _IMPERSONATION_TEMPLATES:
                    if re.search(template.format(name=folded_surname), folded):
                        defects.append(
                            f"impersonation/attribution phrasing about {surname!r} "
                            f"detected: {block[:80]!r} — cite a resolved source instead"
                        )
                        break
        return defects

    def check(
        self,
        findings: list[Finding],
        entries: list[VerificationEntry],
        evidence: list[ToolEvidence],
        prose_blocks: list[str],
    ) -> list[str]:
        return [
            *self.check_completeness(findings, entries),
            *self.check_tool_honesty(entries, evidence),
            *self.check_attribution(prose_blocks),
        ]
