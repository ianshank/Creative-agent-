"""LLM call envelopes: typed call kinds, assembled prompts, raw results.

Each pipeline stage issues calls of one CallKind with its own output schema; the fake
client keys scripted responses by kind (never an ordered queue), and each call's schema
version is part of the wire contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, Field

from creative_agent.models.base import SchemaModel

LLM_CALL_CONTRACT_VERSION = 1


class CallKind(StrEnum):
    """The typed calls the review pipeline makes."""

    CLASSIFY = "classify"
    ROW = "row"
    CLAIMS = "claims"
    SOURCE_QUALITY = "source_quality"
    JUDGEMENT = "judgement"
    SYNTHESIS = "synthesis"


class AssembledPrompt(SchemaModel):
    """One ready-to-send call: prompt text + the schema the response must satisfy."""

    kind: CallKind
    system: str = Field(min_length=1)
    user: str = Field(min_length=1)
    output_schema: dict[str, object]
    allowed_tools: list[str] = Field(default_factory=list)
    fetch_domain_allowlist: list[str] = Field(default_factory=list)
    contract_version: int = LLM_CALL_CONTRACT_VERSION


class ToolEvidence(SchemaModel):
    """One observed tool result from the transcript (honesty cross-check input)."""

    tool_name: str
    target: str = Field(description="URL or path the tool was invoked on")
    ok: bool = Field(description="False when the tool result reported an error")


class RawLLMResult(SchemaModel):
    """What a backend returns for one call, before schema-specific parsing."""

    kind: CallKind
    payload: dict[str, object]
    tool_evidence: list[ToolEvidence] = Field(default_factory=list)
    model: str = ""
    cost_usd: float | None = None


ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_payload(result: RawLLMResult, model_type: type[ModelT]) -> ModelT:
    """Validate a raw payload against its call's output model."""
    return model_type.model_validate(result.payload)
