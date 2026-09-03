"""VerificationLogChecker — the hard rules, enforced mechanically.

An incomplete verification log fails the review (never softened); tool honesty is
checked against observed tool *results*, matched on canonical identifiers so URL noise
neither defeats nor fake-satisfies the check; impersonation/attribution phrasing for
oracle-source authors is swept deterministically.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from creative_agent.harness.canonical import (
    DEFAULT_IDENTIFIER_AUTHORITIES,
    canonicalize,
    fetched_identifier,
)
from creative_agent.harness.llm.base import ToolEvidence
from creative_agent.models.findings import Finding
from creative_agent.models.oracle import DEFAULT_IMPERSONATION_PATTERNS
from creative_agent.models.verification import VerificationEntry

# Tools whose successful results count as "fetched" evidence. WebSearch is deliberately
# absent: search snippets support existence, never content (spec tool-honesty rule).
# Overridable via settings so an MCP fetch tool can be recognized.
DEFAULT_FETCH_TOOLS = frozenset({"WebFetch", "Read"})


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
        impersonation_patterns: tuple[str, ...] = DEFAULT_IMPERSONATION_PATTERNS,
        identifier_authorities: Mapping[str, tuple[str, ...]] = DEFAULT_IDENTIFIER_AUTHORITIES,
    ) -> None:
        self._surnames = sorted({parts[-1] for name in author_names if (parts := name.split())})
        self._fetch_tools = fetch_tools
        self._impersonation_patterns = tuple(impersonation_patterns)
        self._identifier_authorities = identifier_authorities

    def _observed_identifiers(self, evidence: list[ToolEvidence]) -> set[str]:
        """Scholarly identifiers this run actually retrieved from their own registrar.

        Only authority-bound fetches count (DEC-F12). Previously every fetch target was
        canonicalized and the resulting identifier credited, so a decoy URL on any
        allowlisted host, or a local file whose *name* embedded an identifier, both minted
        evidence for a paper that was never retrieved.
        """
        return {
            identifier
            for item in evidence
            if item.ok
            and item.tool_name in self._fetch_tools
            and (identifier := fetched_identifier(item.target, self._identifier_authorities))
        }

    def _observed_targets(self, evidence: list[ToolEvidence]) -> set[str]:
        """The exact strings this run successfully fetched or read.

        Kept for entries that make no scholarly claim — reading the artifact under review
        is legitimate evidence for an assertion about the artifact. It can no longer
        satisfy an entry that names a `canonical_id`; see `check_tool_honesty`.
        """
        return {
            item.target
            for item in evidence
            if item.ok and item.tool_name in self._fetch_tools and item.target
        }

    def check_completeness(
        self, findings: list[Finding], entries: list[VerificationEntry]
    ) -> list[str]:
        """Every LLM doctrinal finding needs a verification entry for each row it cites.

        Deterministic findings are the harness's own mechanical checks — they carry no
        model assertion to verify."""
        defects: list[str] = []
        entry_rows = {e.row_id for e in entries if e.row_id}
        for finding in findings:
            if finding.origin == "deterministic":
                continue
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
        """fetched=True must be backed by a successful fetch-class tool result.

        Claims are matched by kind (DEC-F12). An entry that names a `canonical_id` is a
        scholarly claim and must be satisfied by an identifier this run retrieved from
        that identifier's own registrar; a raw-string match cannot satisfy it, which is
        what let a local `Read` back a claim about a paper. An entry with no
        `canonical_id` makes no scholarly claim and may still be satisfied by an exact
        target match.
        """
        observed_identifiers = self._observed_identifiers(evidence)
        observed_targets = self._observed_targets(evidence)
        defects: list[str] = []
        for index, entry in enumerate(entries):
            if not entry.fetched:
                continue
            claimed_identifier = canonicalize(entry.canonical_id)
            if claimed_identifier is not None:
                if claimed_identifier in observed_identifiers:
                    continue
                defects.append(
                    f"verification entry {index} claims fetched=True for "
                    f"{claimed_identifier} but no successful fetch of that identifier "
                    "from its own registrar was observed; a URL or file path that merely "
                    "contains the identifier is not retrieval"
                )
                continue
            claimed = {
                identifier
                for identifier in (canonicalize(entry.source_url), entry.source_url)
                if identifier
            }
            if not (claimed & (observed_identifiers | observed_targets)):
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
                for template in self._impersonation_patterns:
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
