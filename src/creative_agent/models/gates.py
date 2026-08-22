"""Measurement claims and their gate assessments (spec measurement gates + step 5)."""

from __future__ import annotations

from pydantic import Field

from creative_agent.models.base import SchemaModel


class GateField(SchemaModel):
    """Whether one gate is stated for a claim, with the supporting text if so."""

    stated: bool
    text: str = ""


class MeasurementClaim(SchemaModel):
    """One quantitative/design claim extracted from the artifact.

    `gate_fields` is keyed by gate name from the oracle's gate policy — the model never
    fixes the gate count. `provenance` records the hand-asserted-number requisites
    (dataset/baseline/seeds/interval per oracle data) that the claim states.
    """

    claim: str = Field(min_length=1)
    section: str = ""
    gate_fields: dict[str, GateField] = Field(default_factory=dict)
    provenance: dict[str, bool] = Field(default_factory=dict)

    def missing_gates(self, gate_names: list[str]) -> list[str]:
        return [
            name
            for name in gate_names
            if not self.gate_fields.get(name, GateField(stated=False)).stated
        ]

    def missing_provenance(self, required: list[str]) -> list[str]:
        return [key for key in required if not self.provenance.get(key, False)]


class GateAssessment(SchemaModel):
    """Deterministic verdict for one claim against the oracle's gate policy."""

    claim: str
    missing_gates: list[str] = Field(default_factory=list)
    missing_provenance: list[str] = Field(default_factory=list)
    blueprint_blocker_gates: list[str] = Field(default_factory=list)
