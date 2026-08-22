"""Review request/result envelopes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from creative_agent.models.base import SchemaModel
from creative_agent.models.findings import Finding
from creative_agent.models.state import EscalationEvent

ReviewMode = Literal["conformance", "advisory"]
ModeSelection = Literal["auto", "conformance", "advisory"]


class ReviewRequest(SchemaModel):
    """Inputs to one review run."""

    artifact_path: Path
    artifact_id: str = Field(min_length=1)
    oracle_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    mode: ModeSelection = "auto"
    artifact_repo: Path | None = None
    offline: bool = False


class ReviewResult(SchemaModel):
    """Outcome of a run, before rendering."""

    mode: ReviewMode
    mode_uncertain: bool = False
    artifact_class: str
    findings: list[Finding] = Field(default_factory=list)
    escalation: EscalationEvent | None = None
