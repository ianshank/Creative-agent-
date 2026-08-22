"""FileStateStore round-trip, corruption handling, BC fixture; CycleEscalator matrix."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from creative_agent.errors import StateCorruptError
from creative_agent.harness.state import CycleEscalator, FileStateStore
from creative_agent.models.findings import Severity
from creative_agent.models.review import ReviewResult
from creative_agent.models.state import CycleRecord, HistoricalFinding, ReviewState
from tests.factories import make_finding, make_key, make_oracle

WHEN = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def record(cycle: int, keys: list[tuple[str, str]], disposition: str = "open") -> CycleRecord:
    return CycleRecord(
        cycle=cycle,
        completed_at=WHEN,
        mode="conformance",
        findings=[
            HistoricalFinding(
                key=make_key(row, slug),
                severity=Severity.MAJOR,
                disposition=disposition,  # type: ignore[arg-type]
            )
            for row, slug in keys
        ],
    )


def state_with_history(records: list[CycleRecord]) -> ReviewState:
    return ReviewState(artifact_id="artifact-x", cycle=len(records), history=records)


def result_with(keys: list[tuple[str, str]], severity: Severity = Severity.MAJOR) -> ReviewResult:
    return ReviewResult(
        mode="conformance",
        artifact_class="architecture_design",
        findings=[
            make_finding(
                finding_id=f"F{i}",
                severity=severity,
                original_severity=severity,
                key=make_key(row, slug),
            )
            for i, (row, slug) in enumerate(keys)
        ],
    )


class TestStateStore:
    def test_missing_file_yields_fresh_state(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        state = store.load("new-artifact")
        assert state.cycle == 0 and state.history == []

    def test_round_trip(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        state = state_with_history([record(1, [("D1", "gap")])])
        path = store.save(state, "## Cycle 1 summary\n")
        assert path.read_text(encoding="utf-8").startswith("---\n")
        loaded = store.load("artifact-x")
        assert loaded == state

    def test_summary_body_is_ignored_on_load(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        store.save(state_with_history([]), "arbitrary human text\ncycle: 99\n")
        assert store.load("artifact-x").cycle == 0

    def test_corrupt_front_matter_is_typed_error(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        store.path_for("bad").parent.mkdir(parents=True, exist_ok=True)
        store.path_for("bad").write_text("no front matter here", encoding="utf-8")
        with pytest.raises(StateCorruptError, match="--reset-state"):
            store.load("bad")

    def test_unsupported_schema_version_rejected(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        store.path_for("v9").parent.mkdir(parents=True, exist_ok=True)
        store.path_for("v9").write_text(
            "---\nschema_version: 9\nartifact_id: v9\ncycle: 0\nhistory: []\n---\n",
            encoding="utf-8",
        )
        with pytest.raises(StateCorruptError, match="schema_version"):
            store.load("v9")

    def test_reset_removes_state(self, tmp_path: Path) -> None:
        store = FileStateStore(tmp_path)
        store.save(state_with_history([]))
        store.reset("artifact-x")
        assert store.load("artifact-x").cycle == 0

    def test_frozen_v1_fixture_loads_forever(self, tmp_path: Path) -> None:
        """BC gate: the committed v1 state fixture must always load."""
        fixture = Path(__file__).parent.parent / "fixtures" / "state" / "v1-example.md"
        store = FileStateStore(fixture.parent)
        state = store.load("v1-example")
        assert state.schema_version == 1
        assert state.cycle == 2
        assert state.history[0].findings[0].disposition == "open"
        assert state.history[1].findings[0].disposition == "addressed"


class TestCycleEscalator:
    def escalator(self) -> CycleEscalator:
        return CycleEscalator(make_oracle())

    def test_cycle_three_recurring_open_major_fires(self) -> None:
        state = state_with_history([record(1, [("D1", "gap")]), record(2, [("D1", "gap")])])
        event = self.escalator().check(state, result_with([("D1", "gap")]))
        assert event is not None
        assert event.kind == "charter_review"
        assert event.cycles == [1, 2, 3]
        assert "charter review" in event.message

    def test_cycle_two_does_not_fire(self) -> None:
        state = state_with_history([record(1, [("D1", "gap")])])
        assert self.escalator().check(state, result_with([("D1", "gap")])) is None

    def test_changed_slug_does_not_fire(self) -> None:
        state = state_with_history([record(1, [("D1", "gap")]), record(2, [("D1", "gap")])])
        assert self.escalator().check(state, result_with([("D1", "other-gap")])) is None

    def test_recurring_minor_does_not_fire(self) -> None:
        state = state_with_history([record(1, [("D1", "gap")]), record(2, [("D1", "gap")])])
        result = result_with([("D1", "gap")], severity=Severity.MINOR)
        assert self.escalator().check(state, result) is None

    def test_addressed_disposition_does_not_fire(self) -> None:
        """Owner-addressed findings are not 'recurring' — dispositions matter."""
        state = state_with_history(
            [
                record(1, [("D1", "gap")], disposition="addressed"),
                record(2, [("D1", "gap")], disposition="addressed"),
            ]
        )
        assert self.escalator().check(state, result_with([("D1", "gap")])) is None

    def test_waived_disposition_does_not_fire(self) -> None:
        state = state_with_history(
            [
                record(1, [("D1", "gap")], disposition="waived"),
                record(2, [("D1", "gap")], disposition="open"),
            ]
        )
        assert self.escalator().check(state, result_with([("D1", "gap")])) is None
