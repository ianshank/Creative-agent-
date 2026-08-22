"""Durable per-artifact review state (DEC-F4). schema_version'd; BC-tested forever."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from creative_agent.models.base import SchemaModel
from creative_agent.models.findings import Disposition, FindingKey, Severity, SeverityField

STATE_SCHEMA_VERSION = 1


class HistoricalFinding(SchemaModel):
    """A finding as recorded in a past cycle: key, severity, and its disposition."""

    key: FindingKey
    severity: SeverityField
    disposition: Disposition = "open"
    summary: str = ""


class CycleRecord(SchemaModel):
    """One completed review cycle."""

    cycle: int = Field(ge=1)
    completed_at: datetime
    mode: Literal["conformance", "advisory"]
    artifact_class: str = ""
    content_sha256: str = ""
    findings: list[HistoricalFinding] = Field(default_factory=list)


class EscalationEvent(SchemaModel):
    """Charter-review STOP: a Major recurred at/beyond the oracle's escalation cycle."""

    kind: Literal["charter_review"] = "charter_review"
    key: FindingKey
    cycles: list[int] = Field(min_length=2)
    message: str = ""


class ReviewState(SchemaModel):
    """Everything the harness remembers about one artifact across cycles."""

    schema_version: int = STATE_SCHEMA_VERSION
    artifact_id: str = Field(min_length=1)
    cycle: int = Field(ge=0, default=0, description="Number of completed cycles")
    history: list[CycleRecord] = Field(default_factory=list)

    def open_major_keys(self) -> dict[str, list[int]]:
        """Map rendered FindingKey -> cycles where it appeared as an open Major+."""
        occurrences: dict[str, list[int]] = {}
        for record in self.history:
            for finding in record.findings:
                if finding.severity >= Severity.MAJOR and finding.disposition == "open":
                    occurrences.setdefault(finding.key.render(), []).append(record.cycle)
        return occurrences
