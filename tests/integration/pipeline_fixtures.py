"""Shared builders for pipeline integration tests: a stub agent + scripted responses."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from creative_agent.config import HarnessSettings
from creative_agent.harness.clock import FixedClock
from creative_agent.harness.llm.base import CallKind, RawLLMResult, ToolEvidence
from creative_agent.harness.llm.fake import FakeLLMClient, script_key
from creative_agent.harness.pipeline import ReviewPipeline
from creative_agent.harness.state import FileStateStore
from creative_agent.models.oracle import OracleTable
from creative_agent.models.review import ReviewRequest
from creative_agent.models.state import ReviewState
from tests.factories import make_oracle

PINNED_NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

CONFORMANT_ARTIFACT = """# A Mini Design

This design conforms to the Mini Program research corpus.

## Safety

A certified fallback controller preempts the learner.
"""

PLAIN_ARTIFACT = """# A Plain Design

Nothing here claims any research program membership.
"""


class StubAgent:
    name = "stub"

    def default_oracle(self) -> str:
        return "mini"

    def prompt_template_dir(self) -> str:
        return "default"

    def build_context(
        self, request: ReviewRequest, oracle: OracleTable, state: ReviewState
    ) -> dict[str, object]:
        return {}


def raw(kind: CallKind, payload: dict[str, Any], **kwargs: Any) -> RawLLMResult:
    return RawLLMResult(kind=kind, payload=payload, **kwargs)


def classify_payload(
    *,
    artifact_class: str = "architecture_design",
    recommendation: str = "conformance",
    evidence: str | None = "This design conforms to the Mini Program research corpus.",
    safety: bool = True,
) -> dict[str, Any]:
    return {
        "artifact_class": artifact_class,
        "mode_recommendation": recommendation,
        "conformance_evidence": evidence,
        "safety_section_present": safety,
        "rationale": "test",
    }


def row_payload(
    row_id: str,
    verdict: str = "hit",
    findings: list[dict[str, Any]] | None = None,
    entries: list[dict[str, Any]] | None = None,
    na_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "verdict": verdict,
        "na_reason": na_reason,
        "evidence_quotes": [],
        "findings": findings or [],
        "verification_entries": entries or [],
    }


def verified_entry(row_id: str, arxiv: str = "2001.00001") -> dict[str, Any]:
    return {
        "assertion": f"Row {row_id} check applied",
        "row_id": row_id,
        "source_url": f"https://arxiv.org/abs/{arxiv}",
        "canonical_id": f"arxiv:{arxiv}",
        "confidence": "Certain",
        "fetched": True,
        "status": "verified",
    }


def finding_payload(
    severity: str = "major",
    anchor: str = "some-defect",
    row_id: str = "D1",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "summary": f"Defect at {anchor}",
        "anchor": anchor,
        "doctrine_refs": [row_id],
        "gate_refs": [],
        "supports": [{"kind": "doctrine_row", "ref": row_id}],
        "disposition_required": "Fix it",
    }


def synthesis_payload(headline: str = "The design has issues.") -> dict[str, Any]:
    return {
        "headline": headline,
        "confidence": "Likely",
        "what_survives": ["The safety envelope"],
        "residual_risks": ["Unmeasured drift"],
    }


def empty_sq_payload() -> dict[str, Any]:
    return {
        "load_bearing_violations": [],
        "regime_break_findings": [],
        "verification_entries": [],
    }


def empty_judgement_payload() -> dict[str, Any]:
    return {
        "baselines": [],
        "falsifiability": [],
        "scope": [],
        "verification_entries": [],
    }


def base_scripts(
    *,
    classify: dict[str, Any] | None = None,
    rows: dict[str, list[dict[str, Any]]] | None = None,
    synthesis_count: int = 1,
    evidence: list[ToolEvidence] | None = None,
) -> dict[str, list[RawLLMResult | Exception]]:
    """Scripts for a mini-oracle run (rows D1, D2), overridable per test."""
    rows = rows or {}
    scripts: dict[str, list[RawLLMResult | Exception]] = {
        script_key(CallKind.CLASSIFY): [raw(CallKind.CLASSIFY, classify or classify_payload())],
        script_key(CallKind.CLAIMS): [raw(CallKind.CLAIMS, {"claims": []})],
        script_key(CallKind.SOURCE_QUALITY): [raw(CallKind.SOURCE_QUALITY, empty_sq_payload())],
        script_key(CallKind.JUDGEMENT): [raw(CallKind.JUDGEMENT, empty_judgement_payload())],
        script_key(CallKind.SYNTHESIS): [
            raw(CallKind.SYNTHESIS, synthesis_payload()) for _ in range(synthesis_count)
        ],
    }
    for row_id in ("D1", "D2"):
        payloads = rows.get(row_id, [row_payload(row_id)])
        scripts[script_key(CallKind.ROW, row_id)] = [
            raw(CallKind.ROW, p, tool_evidence=evidence or []) for p in payloads
        ]
    return scripts


def build_pipeline(
    tmp_path: Path,
    fake: FakeLLMClient,
    oracle: OracleTable | None = None,
    max_regen: int = 1,
) -> tuple[ReviewPipeline, FileStateStore]:
    settings = HarnessSettings(
        review_log_dir=tmp_path / "review-log",
        max_regeneration_attempts=max_regen,
    )
    store = FileStateStore(settings.review_log_dir)
    pipeline = ReviewPipeline(
        agent=StubAgent(),
        oracle=oracle or make_oracle(),
        llm=fake,
        settings=settings,
        state_store=store,
        clock=FixedClock(PINNED_NOW),
    )
    return pipeline, store


def request_for(tmp_path: Path, text: str, **kwargs: Any) -> ReviewRequest:
    artifact = tmp_path / "design.md"
    artifact.write_text(text, encoding="utf-8")
    defaults: dict[str, Any] = {
        "artifact_path": artifact,
        "artifact_id": "design",
        "oracle_id": "mini",
        "agent_name": "stub",
        "mode": "auto",
    }
    return ReviewRequest(**{**defaults, **kwargs})
