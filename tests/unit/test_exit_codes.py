"""The review outcome → exit code contract, as a table (DEC-F38).

`errors.py` calls the exit-code table "a frozen contract ... CI consumers depend on these
codes". Codes 3, 4, 5 and 6 are raised and `test_errors.py` maps them. Codes 0, 1 and 2
are *outcomes*, and while they were computed inline in a Typer command body the only way
to reach them was to drive the CLI with a monkeypatched pipeline. Exit 2 got covered that
way twice; exit 1 was covered not at all, and deleting its two lines left the whole suite
green — a conformance review with Major findings and no Blocker would have exited 0, and
every CI consumer branching on 1 reads that as clean.

The first attempt at fixing this was another monkeypatched CLI test, and it was *itself*
weak: `scripts/verify_guard.py` reported GUARD IS WEAK for it. Extracting the mapping was
the right answer to both problems — the contract is a function of the result, so it should
be tested as one.
"""

from __future__ import annotations

import pytest

from creative_agent.errors import ExitCode
from creative_agent.harness.exitcodes import exit_code_for
from creative_agent.models.findings import Severity
from creative_agent.models.review import ReviewResult
from creative_agent.models.state import EscalationEvent
from tests.factories import make_finding, make_key


def result(*severities: Severity, escalation: EscalationEvent | None = None) -> ReviewResult:
    return ReviewResult(
        mode="conformance",
        artifact_class="architecture_design",
        findings=[
            make_finding(finding_id=f"F{i}", severity=s, key=make_key(slug=f"defect-{i}"))
            for i, s in enumerate(severities, start=1)
        ],
        escalation=escalation,
    )


ESCALATION = EscalationEvent(
    key=make_key(slug="recurring"), cycles=[1, 2, 3], message="STOP — charter review"
)


class TestTheExitCodeTable:
    @pytest.mark.parametrize(
        ("severities", "escalation", "expected"),
        [
            # Clean: nothing, or nothing above Info.
            ((), None, ExitCode.CLEAN),
            ((Severity.INFO,), None, ExitCode.CLEAN),
            ((Severity.MINOR, Severity.INFO), None, ExitCode.CLEAN),
            # Major and above, no Blocker, no escalation.
            ((Severity.MAJOR,), None, ExitCode.FINDINGS_MAJOR),
            ((Severity.INFO, Severity.MAJOR), None, ExitCode.FINDINGS_MAJOR),
            ((Severity.MAJOR, Severity.MINOR), None, ExitCode.FINDINGS_MAJOR),
            # Blocker wins over Major.
            ((Severity.BLOCKER,), None, ExitCode.BLOCKER_OR_STOP),
            ((Severity.MAJOR, Severity.BLOCKER), None, ExitCode.BLOCKER_OR_STOP),
            # Escalation is not a severity: it wins from any finding set, including none.
            ((), ESCALATION, ExitCode.BLOCKER_OR_STOP),
            ((Severity.INFO,), ESCALATION, ExitCode.BLOCKER_OR_STOP),
            ((Severity.MAJOR,), ESCALATION, ExitCode.BLOCKER_OR_STOP),
        ],
    )
    def test_the_published_code(
        self,
        severities: tuple[Severity, ...],
        escalation: EscalationEvent | None,
        expected: ExitCode,
    ) -> None:
        assert exit_code_for(result(*severities, escalation=escalation)) is expected

    def test_the_escalation_disjunct_is_independent_of_severity(self) -> None:
        """The half that was never exercised, stated on its own.

        A cycle-3 STOP on an Info-only run must still exit 2. Every CLI test that reached
        exit 2 did so through a Blocker, so reducing the condition to the Blocker check
        alone left the suite green — and the charter-review hand-off, which is the entire
        point of escalation, would never have reached CI.
        """
        info_only = result(Severity.INFO)
        assert exit_code_for(info_only) is ExitCode.CLEAN
        assert exit_code_for(result(Severity.INFO, escalation=ESCALATION)) is (
            ExitCode.BLOCKER_OR_STOP
        )

    def test_the_major_threshold_is_at_major_not_above_it(self) -> None:
        """`>=`, not `>`. An off-by-one here downgrades every Major review to clean."""
        assert exit_code_for(result(Severity.MINOR)) is ExitCode.CLEAN
        assert exit_code_for(result(Severity.MAJOR)) is ExitCode.FINDINGS_MAJOR
