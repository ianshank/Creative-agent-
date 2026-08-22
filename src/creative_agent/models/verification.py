"""Verification-log entries — the tool-honesty rules are encoded as validators."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from creative_agent.models.base import SchemaModel

ConfidenceTag = Literal["Certain", "Likely", "Guessing"]
VerificationStatus = Literal["verified", "guessing", "unverified_flagged"]


class VerificationEntry(SchemaModel):
    """One line of the verification log: assertion → row → source, with honesty state.

    Tool honesty (spec hard rule): `verified` requires an actually-fetched source —
    `fetched=True` plus a canonical identifier or URL the harness can match against tool
    results. Search snippets support existence only, never content, and never absence.
    """

    assertion: str = Field(min_length=1)
    row_id: str | None = None
    source_url: str | None = None
    canonical_id: str | None = Field(
        default=None, description="Normalized arXiv id or DOI extracted from the source"
    )
    confidence: ConfidenceTag
    fetched: bool = False
    status: VerificationStatus

    @model_validator(mode="after")
    def _honesty(self) -> VerificationEntry:
        if self.status == "verified":
            if not self.fetched:
                raise ValueError("status=verified requires fetched=True")
            if not (self.source_url or self.canonical_id):
                raise ValueError("status=verified requires a source_url or canonical_id")
        if self.status == "guessing" and self.confidence != "Guessing":
            raise ValueError("status=guessing must carry confidence=Guessing")
        return self
