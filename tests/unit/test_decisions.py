"""DecisionLog parsing and the artifact-repo-scoped DecisionGate."""

from pathlib import Path

from creative_agent.harness.decisions import DecisionGate, DecisionLog
from creative_agent.models.findings import Severity
from creative_agent.models.oracle import DecisionTrap
from tests.factories import make_oracle

LOG = """# Decision Log

## DEC-S1 — Reward specification — CONFIRMED

One scalar main-task reward; subtasks are reward-respecting.

## DEC-S2 — Step-size regime — DEFERRED

Per-weight meta step sizes deferred to a later milestone.

## DEC-S3 — Plasticity mechanism — PENDING
"""


def gated_oracle() -> object:
    return make_oracle(
        required_decisions=["DEC-S1", "DEC-S2", "DEC-S3", "DEC-S6"],
        decision_traps=[
            DecisionTrap(
                decision_id="DEC-S6",
                trap="Bounded per-step KL is not a safety property of the vehicle.",
            )
        ],
    )


class TestDecisionLogParse:
    def test_parses_ids_and_statuses(self, tmp_path: Path) -> None:
        path = tmp_path / "decision-log.md"
        path.write_text(LOG, encoding="utf-8")
        assert DecisionLog.parse(path) == {
            "DEC-S1": "CONFIRMED",
            "DEC-S2": "DEFERRED",
            "DEC-S3": "PENDING",
        }

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert DecisionLog.parse(tmp_path / "nope.md") == {}


class TestDecisionGate:
    def test_no_artifact_repo_no_findings(self) -> None:
        gate = DecisionGate(gated_oracle(), "docs/decision-log.md")
        assert gate.check(None) == []

    def test_missing_and_pending_decisions_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "decision-log.md").write_text(LOG, encoding="utf-8")
        gate = DecisionGate(gated_oracle(), "docs/decision-log.md")
        findings = gate.check(tmp_path)
        anchors = {f.anchor for f in findings}
        # DEC-S1 confirmed -> ok; S2 deferred, S3 pending, S6 absent -> flagged.
        assert anchors == {
            "DEC-S2-missing-decision",
            "DEC-S3-missing-decision",
            "DEC-S6-missing-decision",
        }
        assert all(f.severity is Severity.MAJOR for f in findings)

    def test_trap_text_travels_with_its_decision(self, tmp_path: Path) -> None:
        gate = DecisionGate(gated_oracle(), "docs/decision-log.md")
        findings = gate.check(tmp_path)  # no log at all
        s6 = next(f for f in findings if f.anchor.startswith("DEC-S6"))
        assert "not a safety property" in s6.summary

    def test_oracle_without_required_decisions_is_silent(self, tmp_path: Path) -> None:
        gate = DecisionGate(make_oracle(), "docs/decision-log.md")
        assert gate.check(tmp_path) == []
