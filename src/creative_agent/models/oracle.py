"""Oracle tables: the doctrine-as-data schema (DEC-F2).

Everything normative in a review oracle — rows, tiers, sources, severity caps, gates,
source-quality rules, conformance markers, decision requirements, protocol thresholds —
is expressed here so the engine stays corpus-agnostic.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator

from creative_agent.models.base import SchemaModel
from creative_agent.models.findings import Severity, SeverityField

EvidenceTier = Literal["PR", "AP", "T", "E", "NONE"]
"""Peer-reviewed, archival preprint, talk, essay/blog; NONE marks a disclosed gap row."""

_ROW_ID = re.compile(r"^[A-Z]\d+[a-z]?$")


class SourceRef(SchemaModel):
    """One published source backing a doctrine row."""

    citation: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    url: HttpUrl | None = None
    tier: EvidenceTier
    verified: bool = False
    last_verified: date | None = None
    notes: str | None = None


class FreshnessMeta(SchemaModel):
    """Oracle re-baseline bookkeeping; feeds the staleness severity cap."""

    last_rebaselined: date
    rebaseline_count: int = Field(ge=0)
    max_rebaselines_without_verification: int = 2


class OracleRowPrecedence(SchemaModel):
    """Cross-row precedence (e.g. D1 vs D12: mechanism naming is not domain content)."""

    applies_to: str = Field(min_length=1)
    defer_to: str | None = None
    note: str = Field(min_length=1)


class OracleRow(SchemaModel):
    """One doctrine-table row."""

    id: str
    principle: str = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
    tier: EvidenceTier
    check: str = Field(min_length=1)
    check_notes: str | None = None
    failure_mode: str = Field(min_length=1)
    disclosed_gap: bool = False
    precedence: OracleRowPrecedence | None = None

    @field_validator("id")
    @classmethod
    def _id_pattern(cls, value: str) -> str:
        if not _ROW_ID.match(value):
            raise ValueError(f"row id {value!r} does not match ^[A-Z]\\d+[a-z]?$")
        return value

    @model_validator(mode="after")
    def _sources_or_gap(self) -> OracleRow:
        if not self.disclosed_gap:
            if not self.sources:
                raise ValueError(f"row {self.id}: non-gap rows need at least one source")
            # Only PR/AP tiers can carry Blockers, so only they must resolve to an
            # identifier; T/E rows are the spec's disclosed self-exemption and are
            # severity-capped instead.
            for source in self.sources:
                if source.tier in ("PR", "AP") and not (
                    source.doi or source.arxiv_id or source.url
                ):
                    raise ValueError(
                        f"row {self.id}: {source.tier}-tier source {source.citation!r} "
                        "needs doi, arxiv_id, or url"
                    )
        return self

    def is_stale(self, freshness: FreshnessMeta) -> bool:
        """A row is stale when no source was verified within the re-baseline budget."""
        if self.disclosed_gap:
            return False
        if any(s.verified for s in self.sources):
            return False
        return freshness.rebaseline_count >= freshness.max_rebaselines_without_verification


class GateDefinition(SchemaModel):
    """One measurement gate (observable / update rule / compute budget / falsifier...)."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    anchors: list[str] = Field(default_factory=list)
    blueprint_missing_severity: SeverityField | None = None


class GatePolicy(SchemaModel):
    """Gate set plus the severities their absence produces."""

    gates: list[GateDefinition] = Field(min_length=1)
    missing_any_severity: SeverityField
    quant_claim_requires: list[str] = Field(default_factory=list)
    hand_asserted_severity: SeverityField

    @model_validator(mode="after")
    def _unique_gate_names(self) -> GatePolicy:
        names = [g.name for g in self.gates]
        if len(names) != len(set(names)):
            raise ValueError("duplicate gate names")
        return self


class ArtifactClassRule(SchemaModel):
    """Per-artifact-class obligations (protocol step 2)."""

    name: str = Field(min_length=1)
    requires_gates: list[str] = Field(default_factory=list)
    requires_sections: list[str] = Field(default_factory=list)
    missing_section_severity: SeverityField = Severity.BLOCKER
    source_quality_only: bool = False


class TierCap(SchemaModel):
    """Severity cap for findings supported only by rows at these tiers."""

    tiers: list[EvidenceTier] = Field(min_length=1)
    max_solo_severity: SeverityField
    reason: str = Field(min_length=1)


BlockerBasis = Literal[
    "tier_pr_or_ap_row",
    "gate_failure",
    "safety_failure",
    "internal_contradiction",
]


class SeverityPolicyConfig(SchemaModel):
    """Data-driven severity rules (DEC-F3)."""

    tier_caps: list[TierCap] = Field(default_factory=list)
    unverified_row_cap: SeverityField
    blocker_requires_any_of: list[BlockerBasis] = Field(min_length=1)


class SourceQualityConfig(SchemaModel):
    """Deterministic + judgement source-quality rules (spec source-quality protocol)."""

    cluster_citation_min_refs: int = Field(ge=2)
    cluster_citation_severity: SeverityField
    bibliography_hygiene_severity: SeverityField
    vendor_domains: list[str] = Field(default_factory=list)
    vendor_page_note: str = Field(min_length=1)
    load_bearing_requires_tiers: list[EvidenceTier] = Field(min_length=1)
    regime_breaks: list[str] = Field(default_factory=list)


class ConformanceConfig(SchemaModel):
    """Applicability precondition: markers that indicate a conformance claim."""

    markers: list[str] = Field(min_length=1)
    advisory_severity_cap: SeverityField


class LabelElement(SchemaModel):
    """One required element of a claimed label, with its detection patterns."""

    label: str = Field(min_length=1)
    patterns: list[str] = Field(min_length=1, description="casefold substrings, any-of")


class OakConformanceSpec(SchemaModel):
    """The OaK label's required elements, enumerated so a checker can name misses."""

    label_pattern: str = Field(min_length=1, description="regex that detects the label claim")
    doctrine_ref: str = Field(min_length=1, description="row that licenses this check")
    features: list[LabelElement] = Field(min_length=1)
    stages: list[LabelElement] = Field(min_length=1)
    missing_severity: SeverityField


class DecisionTrap(SchemaModel):
    """A named trap the review must probe (e.g. DEC-S6: KL is not a safety property)."""

    decision_id: str = Field(min_length=1)
    trap: str = Field(min_length=1)


class ProtocolConfig(SchemaModel):
    """Review-protocol thresholds and fixed marker text."""

    escalation_cycle: int = Field(ge=2)
    unverified_marker: str = Field(min_length=1)
    usage_gates: list[str] = Field(default_factory=list)
    missing_decision_severity: SeverityField = Severity.MAJOR


class OracleTable(SchemaModel):
    """A complete review oracle."""

    schema_version: int = Field(ge=1)
    oracle_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    conformance: ConformanceConfig
    freshness: FreshnessMeta
    severity_policy: SeverityPolicyConfig
    gate_policy: GatePolicy
    source_quality: SourceQualityConfig
    artifact_classes: list[ArtifactClassRule] = Field(min_length=1)
    required_decisions: list[str] = Field(default_factory=list)
    decision_traps: list[DecisionTrap] = Field(default_factory=list)
    oak_conformance: OakConformanceSpec | None = None
    protocol: ProtocolConfig
    rows: list[OracleRow] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_references(self) -> OracleTable:
        row_ids = [r.id for r in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("duplicate row ids")
        gate_names = {g.name for g in self.gate_policy.gates}
        for rule in self.artifact_classes:
            unknown = set(rule.requires_gates) - gate_names
            if unknown:
                raise ValueError(f"artifact class {rule.name}: unknown gates {sorted(unknown)}")
        class_names = [c.name for c in self.artifact_classes]
        if len(class_names) != len(set(class_names)):
            raise ValueError("duplicate artifact class names")
        for trap in self.decision_traps:
            if trap.decision_id not in self.required_decisions:
                raise ValueError(f"decision trap {trap.decision_id} not in required_decisions")
        if self.oak_conformance is not None and self.oak_conformance.doctrine_ref not in row_ids:
            raise ValueError(
                f"oak_conformance.doctrine_ref {self.oak_conformance.doctrine_ref!r} "
                "is not a known row"
            )
        return self

    def row(self, row_id: str) -> OracleRow:
        for row in self.rows:
            if row.id == row_id:
                return row
        raise KeyError(row_id)

    def source_author_names(self) -> set[str]:
        """All author names across sources — feeds the attribution sweep."""
        return {name for row in self.rows for src in row.sources for name in src.authors}
