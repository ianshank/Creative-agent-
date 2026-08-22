"""Measurement-gate and artifact-class enforcement — fully schema-driven.

Gate names, counts, severities, and per-class obligations come from the oracle's
gate_policy and artifact_classes; nothing here fixes "4 gates" or "safety" in code.
"""

from __future__ import annotations

from creative_agent.models.findings import SupportRef
from creative_agent.models.gates import GateAssessment, MeasurementClaim
from creative_agent.models.oracle import ArtifactClassRule, OracleTable
from creative_agent.models.sweeps import CandidateFinding


class MeasurementGateChecker:
    """Scores extracted claims against the oracle's gates; synthesizes findings."""

    def __init__(self, oracle: OracleTable) -> None:
        self._oracle = oracle
        self._policy = oracle.gate_policy

    def _gate_description(self, name: str) -> str:
        for gate in self._policy.gates:
            if gate.name == name:
                return gate.description
        return name

    def _class_rule(self, artifact_class: str) -> ArtifactClassRule:
        for rule in self._oracle.artifact_classes:
            if rule.name == artifact_class:
                return rule
        raise KeyError(artifact_class)

    def assess(self, claims: list[MeasurementClaim], artifact_class: str) -> list[GateAssessment]:
        rule = self._class_rule(artifact_class)
        if rule.source_quality_only:
            return []
        gate_names = [g.name for g in self._policy.gates]
        blueprint_gates = {
            g.name for g in self._policy.gates if g.blueprint_missing_severity is not None
        }
        assessments: list[GateAssessment] = []
        for claim in claims:
            missing = claim.missing_gates(gate_names)
            assessments.append(
                GateAssessment(
                    claim=claim.claim,
                    missing_gates=missing,
                    missing_provenance=claim.missing_provenance(self._policy.quant_claim_requires),
                    blueprint_blocker_gates=sorted(
                        set(missing) & blueprint_gates & set(rule.requires_gates)
                    ),
                )
            )
        return assessments

    def findings_for(
        self,
        claims: list[MeasurementClaim],
        artifact_class: str,
        sections_present: set[str],
    ) -> list[CandidateFinding]:
        """Deterministic candidate findings for gate misses and class obligations."""
        rule = self._class_rule(artifact_class)
        if rule.source_quality_only:
            return []
        candidates: list[CandidateFinding] = []

        for assessment in self.assess(claims, artifact_class):
            if assessment.blueprint_blocker_gates:
                for gate_name in assessment.blueprint_blocker_gates:
                    definition = next(g for g in self._policy.gates if g.name == gate_name)
                    severity = definition.blueprint_missing_severity
                    assert severity is not None
                    anchors = "; ".join(definition.anchors)
                    candidates.append(
                        CandidateFinding(
                            severity=severity,
                            summary=(
                                f"Claim lacks the {gate_name} gate on a "
                                f"{rule.name}: {assessment.claim}"
                                + (f" (anchor: {anchors})" if anchors else "")
                            ),
                            anchor=f"missing-{gate_name}-{assessment.claim[:40]}",
                            gate_refs=[gate_name],
                            supports=[SupportRef(kind="gate_failure", ref=gate_name)],
                            disposition_required=(
                                f"State the {gate_name} for this claim: {definition.description}"
                            ),
                        )
                    )
            elif assessment.missing_gates:
                candidates.append(
                    CandidateFinding(
                        severity=self._policy.missing_any_severity,
                        summary=(
                            f"Claim missing measurement gate(s) "
                            f"{', '.join(assessment.missing_gates)}: {assessment.claim}"
                        ),
                        anchor=f"missing-gates-{assessment.claim[:40]}",
                        gate_refs=assessment.missing_gates,
                        supports=[
                            SupportRef(kind="gate_failure", ref=name)
                            for name in assessment.missing_gates
                        ],
                        disposition_required=(
                            "State every missing gate: "
                            + ", ".join(
                                f"{name} ({self._gate_description(name)})"
                                for name in assessment.missing_gates
                            )
                        ),
                    )
                )
            if assessment.missing_provenance:
                candidates.append(
                    CandidateFinding(
                        severity=self._policy.hand_asserted_severity,
                        summary=(
                            "Hand-asserted number (missing "
                            f"{', '.join(assessment.missing_provenance)}): "
                            f"{assessment.claim}"
                        ),
                        anchor=f"hand-asserted-{assessment.claim[:40]}",
                        gate_refs=[],
                        supports=[SupportRef(kind="gate_failure", ref="provenance")],
                        disposition_required=(
                            "A number with no dataset, baseline, seed count, or interval "
                            "is hand-asserted, regardless of plausibility."
                        ),
                    )
                )

        for section in rule.requires_sections:
            if section not in sections_present:
                candidates.append(
                    CandidateFinding(
                        severity=rule.missing_section_severity,
                        summary=(f"A {rule.name} must carry a {section} section; none found."),
                        anchor=f"missing-{section}-section",
                        supports=[SupportRef(kind=rule.missing_section_support, ref=section)],
                        disposition_required=f"Add the required {section} section.",
                    )
                )
        return candidates
