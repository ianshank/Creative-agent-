"""Test factories (SQE review M3): tests state only what they care about.

Raw JSON/YAML fixtures are reserved for wire-format and BC tests; everything else builds
models through these helpers so schema evolution doesn't rot the suite.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from creative_agent.models.findings import Finding, FindingKey, Severity, SupportRef
from creative_agent.models.oracle import (
    ArtifactClassRule,
    ConformanceConfig,
    FreshnessMeta,
    GateDefinition,
    GatePolicy,
    OracleRow,
    OracleTable,
    ProtocolConfig,
    SeverityPolicyConfig,
    SourceQualityConfig,
    SourceRef,
    TierCap,
)
from creative_agent.models.sweeps import CandidateFinding
from creative_agent.models.verification import VerificationEntry


def make_source(**overrides: Any) -> SourceRef:
    defaults: dict[str, Any] = {
        "citation": "Doe, A Paper, Journal 2020",
        "authors": ["Jane Doe"],
        "arxiv_id": "2001.00001",
        "tier": "PR",
        "verified": True,
        "last_verified": date(2026, 1, 1),
    }
    return SourceRef(**{**defaults, **overrides})


def make_row(row_id: str = "D1", **overrides: Any) -> OracleRow:
    defaults: dict[str, Any] = {
        "id": row_id,
        "principle": "A principle",
        "sources": [make_source()],
        "tier": "PR",
        "check": "A licensed check",
        "failure_mode": "A failure mode",
    }
    return OracleRow(**{**defaults, **overrides})


def make_oracle(**overrides: Any) -> OracleTable:
    defaults: dict[str, Any] = {
        "schema_version": 1,
        "oracle_id": "mini",
        "name": "mini oracle",
        "version": "1.0",
        "description": "synthetic oracle for engine tests",
        "conformance": ConformanceConfig(
            markers=["Mini Program"], advisory_severity_cap=Severity.INFO
        ),
        # Grace budget of zero matches the shipped default (DEC-F13): an unverified row is
        # stale immediately. `make_source` defaults to `verified=True`, so a row built
        # without an explicit unverified source is unaffected — a test that wants the
        # staleness cap has to ask for it, which is the point.
        "freshness": FreshnessMeta(
            last_rebaselined=date(2026, 1, 1),
            rebaseline_count=0,
            max_rebaselines_without_verification=0,
        ),
        "severity_policy": SeverityPolicyConfig(
            tier_caps=[
                TierCap(tiers=["T", "E"], max_solo_severity=Severity.MAJOR, reason="T/E cap")
            ],
            unverified_row_cap=Severity.MINOR,
            blocker_requires_any_of=[
                "tier_pr_or_ap_row",
                "gate_failure",
                "safety_failure",
                "internal_contradiction",
            ],
        ),
        "gate_policy": GatePolicy(
            gates=[
                GateDefinition(name="observable", description="what is sensed"),
                GateDefinition(
                    name="compute_budget",
                    description="flops per step",
                    blueprint_missing_severity=Severity.BLOCKER,
                ),
                GateDefinition(name="falsifier", description="what would refute"),
            ],
            missing_any_severity=Severity.MAJOR,
            quant_claim_requires=["dataset", "baseline"],
            hand_asserted_severity=Severity.MAJOR,
        ),
        "source_quality": SourceQualityConfig(
            cluster_citation_min_refs=4,
            cluster_citation_severity=Severity.MAJOR,
            bibliography_hygiene_severity=Severity.MINOR,
            vendor_domains=["vendor.example"],
            vendor_page_note="Vendor pages are not evidence.",
            load_bearing_requires_tiers=["PR", "AP"],
            regime_breaks=["supervised -> reinforcement learning"],
        ),
        "artifact_classes": [
            ArtifactClassRule(name="architecture_design"),
            ArtifactClassRule(
                name="deployment_blueprint",
                requires_gates=["compute_budget"],
                requires_sections=["safety"],
            ),
            ArtifactClassRule(name="research_synthesis", source_quality_only=True),
        ],
        "protocol": ProtocolConfig(
            escalation_cycle=3,
            unverified_marker="[Unverified — flagged for human check]",
        ),
        "rows": [make_row("D1"), make_row("D2", tier="E", sources=[make_source(tier="E")])],
    }
    return OracleTable(**{**defaults, **overrides})


def make_key(row_id: str = "D1", slug: str = "some-defect") -> FindingKey:
    return FindingKey(row_id=row_id, slug=slug)


def make_finding(**overrides: Any) -> Finding:
    defaults: dict[str, Any] = {
        "finding_id": "F1",
        "severity": Severity.MAJOR,
        "original_severity": Severity.MAJOR,
        "summary": "A defect",
        "doctrine_refs": ["D1"],
        "supports": [SupportRef(kind="doctrine_row", ref="D1")],
        "key": make_key(),
    }
    return Finding(**{**defaults, **overrides})


def make_candidate(**overrides: Any) -> CandidateFinding:
    defaults: dict[str, Any] = {
        "severity": Severity.MAJOR,
        "summary": "A defect",
        "anchor": "some defect",
        "doctrine_refs": ["D1"],
        "supports": [SupportRef(kind="doctrine_row", ref="D1")],
    }
    return CandidateFinding(**{**defaults, **overrides})


def make_verification(**overrides: Any) -> VerificationEntry:
    defaults: dict[str, Any] = {
        "assertion": "Row D1 says X",
        "row_id": "D1",
        "source_url": "https://arxiv.org/abs/2001.00001",
        "canonical_id": "arxiv:2001.00001",
        "confidence": "Certain",
        "fetched": True,
        "status": "verified",
    }
    return VerificationEntry(**{**defaults, **overrides})
