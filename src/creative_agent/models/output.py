"""The rendered-report contract (spec output format). contract_version'd."""

from __future__ import annotations

from pydantic import Field

from creative_agent.models.base import SchemaModel
from creative_agent.models.findings import Finding
from creative_agent.models.review import ReviewMode
from creative_agent.models.state import EscalationEvent
from creative_agent.models.sweeps import RowDisposition, ScopeItem
from creative_agent.models.verification import ConfidenceTag, VerificationEntry

REPORT_CONTRACT_VERSION = 1


class Verdict(SchemaModel):
    """The verdict line: mode + confidence + headline."""

    mode: ReviewMode
    mode_uncertain: bool = False
    confidence: ConfidenceTag
    headline: str = Field(min_length=1)


class ReviewReport(SchemaModel):
    """Everything the renderer publishes. LLM prose fields are laundered (DEC-F9)."""

    contract_version: int = REPORT_CONTRACT_VERSION
    artifact_id: str
    oracle_id: str
    oracle_version: str
    cycle: int
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    row_dispositions: list[RowDisposition] = Field(default_factory=list)
    what_survives: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    scope_items: list[ScopeItem] = Field(default_factory=list)
    verification_log: list[VerificationEntry] = Field(default_factory=list)
    escalation: EscalationEvent | None = None
