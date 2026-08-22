"""Structured outputs of the LLM sweep calls (doctrine rows, steps 7-9, classify)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from creative_agent.models.base import SchemaModel
from creative_agent.models.findings import SeverityField, SupportRef
from creative_agent.models.gates import MeasurementClaim
from creative_agent.models.verification import VerificationEntry

RowVerdict = Literal["hit", "miss", "not_applicable"]


class CandidateFinding(SchemaModel):
    """A finding as proposed by the LLM, before deterministic assembly and capping."""

    severity: SeverityField
    summary: str = Field(min_length=1)
    anchor: str = Field(min_length=1, description="Short stable phrase naming the defect site")
    doctrine_refs: list[str] = Field(default_factory=list)
    gate_refs: list[str] = Field(default_factory=list)
    supports: list[SupportRef] = Field(default_factory=list)
    disposition_required: str = ""


class RowDisposition(SchemaModel):
    """Protocol step 4: one row's hit/miss/N-A verdict; N-A demands a reason."""

    row_id: str = Field(min_length=1)
    verdict: RowVerdict
    na_reason: str | None = None
    evidence_quotes: list[str] = Field(default_factory=list)
    findings: list[CandidateFinding] = Field(default_factory=list)
    verification_entries: list[VerificationEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _na_needs_reason(self) -> RowDisposition:
        if self.verdict == "not_applicable" and not (self.na_reason or "").strip():
            raise ValueError(f"row {self.row_id}: not_applicable requires na_reason")
        return self


class ClassifyResult(SchemaModel):
    """Protocol steps 2-3: artifact class + conformance evidence."""

    artifact_class: str = Field(min_length=1)
    mode_recommendation: Literal["conformance", "advisory"]
    conformance_evidence: str | None = Field(
        default=None, description="Verbatim quote of the artifact's conformance claim"
    )
    safety_section_present: bool = False
    rationale: str = ""


class ClaimsResult(SchemaModel):
    """Protocol step 5 input: extracted measurement claims."""

    claims: list[MeasurementClaim] = Field(default_factory=list)


class BaselineCheck(SchemaModel):
    """Protocol step 7: claimed advantage vs the simplest plausibly-matching method."""

    advantage: str = Field(min_length=1)
    simplest_baseline: str = Field(min_length=1)
    compared_in_artifact: bool
    finding: CandidateFinding | None = None


class FalsifiabilityCheck(SchemaModel):
    """Protocol step 8: is each prediction's threshold above what a null model produces."""

    prediction: str = Field(min_length=1)
    surprising_result: str = Field(min_length=1)
    above_null: bool
    finding: CandidateFinding | None = None


class ScopeItem(SchemaModel):
    """Protocol step 9: referenced-but-unsupplied dependency, stated as unverified."""

    reference: str = Field(min_length=1)
    supplied: bool
    treated_as_unverified: bool


class JudgementSweepResult(SchemaModel):
    """Combined result of the step 7-9 sweeps plus their verification entries."""

    baselines: list[BaselineCheck] = Field(default_factory=list)
    falsifiability: list[FalsifiabilityCheck] = Field(default_factory=list)
    scope: list[ScopeItem] = Field(default_factory=list)
    verification_entries: list[VerificationEntry] = Field(default_factory=list)


class SourceQualityResult(SchemaModel):
    """LLM half of the source-quality protocol (load-bearing map, regime breaks)."""

    load_bearing_violations: list[CandidateFinding] = Field(default_factory=list)
    regime_break_findings: list[CandidateFinding] = Field(default_factory=list)
    verification_entries: list[VerificationEntry] = Field(default_factory=list)


class SynthesisResult(SchemaModel):
    """Final prose-only call: never findings — those are assembled deterministically."""

    headline: str = Field(min_length=1)
    confidence: Literal["Certain", "Likely", "Guessing"]
    what_survives: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
