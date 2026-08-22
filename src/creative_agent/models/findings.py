"""Findings, severities, and the recurrence key used for cycle escalation."""

from __future__ import annotations

import re
import unicodedata
from enum import IntEnum
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, field_validator

from creative_agent.models.base import SchemaModel

Disposition = Literal["open", "addressed", "disputed", "waived"]


class Severity(IntEnum):
    """Ordered severity; capping compares numerically (DEC-F3)."""

    INFO = 0
    MINOR = 1
    MAJOR = 2
    BLOCKER = 3

    @classmethod
    def parse(cls, value: Any) -> Severity:
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            try:
                return cls[value.strip().upper()]
            except KeyError as exc:
                raise ValueError(f"unknown severity: {value!r}") from exc
        raise ValueError(f"cannot parse severity from {type(value).__name__}")


SeverityField = Annotated[Severity, BeforeValidator(Severity.parse)]

# Support kinds a finding may rest on; blocker legitimacy is judged over these (DEC-F3).
SupportKind = Literal[
    "doctrine_row",
    "gate_failure",
    "safety_failure",
    "internal_contradiction",
]


class SupportRef(SchemaModel):
    """One piece of support for a finding."""

    kind: SupportKind
    ref: str = Field(min_length=1, description="Row ID, gate name, section name, or symbol")


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def normalize_slug(text: str) -> str:
    """Deterministic slug: NFKD-fold, lowercase, non-alnum runs collapse to hyphens."""
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").casefold()
    return _SLUG_STRIP.sub("-", ascii_text).strip("-")


class FindingKey(SchemaModel):
    """Stable identity for recurrence tracking: doctrine row + normalized anchor slug."""

    row_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)

    @field_validator("slug")
    @classmethod
    def _slug_is_normalized(cls, value: str) -> str:
        normalized = normalize_slug(value)
        if not normalized:
            raise ValueError("slug normalizes to empty")
        return normalized

    def render(self) -> str:
        return f"{self.row_id}+{self.slug}"


FindingOrigin = Literal["llm", "deterministic"]


class Finding(SchemaModel):
    """One review finding, after deterministic assembly and capping."""

    finding_id: str = Field(min_length=1)
    origin: FindingOrigin = "llm"
    severity: SeverityField
    original_severity: SeverityField
    summary: str = Field(min_length=1)
    doctrine_refs: list[str] = Field(default_factory=list)
    gate_refs: list[str] = Field(default_factory=list)
    supports: list[SupportRef] = Field(default_factory=list)
    disposition_required: str = ""
    key: FindingKey
    cap_reason: str | None = None
