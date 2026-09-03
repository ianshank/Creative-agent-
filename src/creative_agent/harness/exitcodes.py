"""The review outcome → exit code mapping (DEC-F38).

`errors.py` calls the exit-code table "a frozen contract ... CI consumers depend on these
codes". Three of those codes — 0, 1 and 2 — are *review outcomes* rather than exceptions,
so `tests/unit/test_errors.py`, which maps exception types to codes, cannot reach them.
They were computed inline in a Typer command body, where the only way to test them was to
drive the whole CLI with a monkeypatched pipeline. Exit 2 was covered twice that way and
exit 1 was covered not at all: deleting its two lines left the suite green, so a
conformance review with Major findings and no Blocker would have exited 0 and every CI
consumer branching on 1 would have read that as clean.

A frozen contract deserves a function and a table, not a branch buried in a command.
"""

from __future__ import annotations

from creative_agent.errors import ExitCode
from creative_agent.models.findings import Severity
from creative_agent.models.review import ReviewResult


def exit_code_for(result: ReviewResult) -> ExitCode:
    """The published exit code for a review that completed.

    Ordered most severe first, and deliberately not `elif`-free: the two disjuncts of the
    `BLOCKER_OR_STOP` case are independent, and only one of them was ever exercised.

    - **2** — a Blocker survived capping, *or* the cycle escalator raised a charter-review
      STOP. The escalation half is not a severity: a run whose findings are all Info still
      exits 2 when the same Major has recurred for three cycles, because the hand-off to a
      human is the point of escalation and CI is where it has to land.
    - **1** — something at Major or above, with no Blocker and no escalation.
    - **0** — Info-only, or nothing. An offline run reaches here by construction, which is
      why it prints a banner saying so.

    Aborts (6), review failures (3) and configuration errors (4) never reach this function:
    they are raised, and `errors.py` owns their mapping.
    """
    severities = [Severity.parse(finding.severity) for finding in result.findings]
    if result.escalation is not None or Severity.BLOCKER in severities:
        return ExitCode.BLOCKER_OR_STOP
    if any(severity >= Severity.MAJOR for severity in severities):
        return ExitCode.FINDINGS_MAJOR
    return ExitCode.CLEAN
