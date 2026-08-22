"""MeasurementGateChecker: gate matrix, blueprint blocker, synthesis exemption."""

import pytest

from creative_agent.harness.gates import MeasurementGateChecker
from creative_agent.models.findings import Severity
from creative_agent.models.gates import GateField, MeasurementClaim
from tests.factories import make_oracle


def claim(stated: list[str], provenance: dict[str, bool] | None = None) -> MeasurementClaim:
    return MeasurementClaim(
        claim="latency is 4ms at 100Hz",
        gate_fields={name: GateField(stated=True, text="…") for name in stated},
        provenance=provenance or {"dataset": True, "baseline": True},
    )


@pytest.fixture()
def checker() -> MeasurementGateChecker:
    return MeasurementGateChecker(make_oracle())


class TestGateAssessment:
    def test_complete_claim_produces_no_findings(self, checker: MeasurementGateChecker) -> None:
        claims = [claim(["observable", "compute_budget", "falsifier"])]
        assert checker.findings_for(claims, "architecture_design", {"safety"}) == []

    def test_missing_gate_is_major(self, checker: MeasurementGateChecker) -> None:
        claims = [claim(["observable", "compute_budget"])]
        findings = checker.findings_for(claims, "architecture_design", set())
        assert len(findings) == 1
        assert findings[0].severity is Severity.MAJOR
        assert "falsifier" in findings[0].summary
        assert findings[0].supports[0].kind == "gate_failure"

    def test_blueprint_missing_compute_budget_is_blocker(
        self, checker: MeasurementGateChecker
    ) -> None:
        claims = [claim(["observable", "falsifier"])]
        findings = checker.findings_for(claims, "deployment_blueprint", {"safety"})
        blockers = [f for f in findings if f.severity is Severity.BLOCKER]
        assert len(blockers) == 1
        assert "compute_budget" in blockers[0].summary

    def test_architecture_missing_compute_budget_is_only_major(
        self, checker: MeasurementGateChecker
    ) -> None:
        # blueprint_missing_severity binds only to classes that require the gate.
        claims = [claim(["observable", "falsifier"])]
        findings = checker.findings_for(claims, "architecture_design", set())
        assert all(f.severity is Severity.MAJOR for f in findings)

    def test_synthesis_exempt_from_gates(self, checker: MeasurementGateChecker) -> None:
        claims = [claim([])]
        assert checker.findings_for(claims, "research_synthesis", set()) == []
        assert checker.assess(claims, "research_synthesis") == []

    def test_hand_asserted_number_flagged(self, checker: MeasurementGateChecker) -> None:
        claims = [
            claim(
                ["observable", "compute_budget", "falsifier"],
                provenance={"dataset": True, "baseline": False},
            )
        ]
        findings = checker.findings_for(claims, "architecture_design", set())
        assert len(findings) == 1
        assert "Hand-asserted" in findings[0].summary
        assert "baseline" in findings[0].summary

    def test_missing_safety_section_on_blueprint(self, checker: MeasurementGateChecker) -> None:
        findings = checker.findings_for([], "deployment_blueprint", set())
        assert len(findings) == 1
        assert findings[0].severity is Severity.BLOCKER
        assert findings[0].supports[0].kind == "safety_failure"

    def test_unknown_artifact_class_raises(self, checker: MeasurementGateChecker) -> None:
        with pytest.raises(KeyError):
            checker.findings_for([], "poem", set())
