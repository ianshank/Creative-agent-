"""The exception→exit-code mapping is a frozen contract (README documents it).

Frozen means a change must be deliberate and visible, not that it can never change: the
value list below is the tripwire, so adding a code fails this test until someone updates
it alongside the README, the architecture doc and a decision-log entry. `RUN_ABORTED = 6`
was added under DEC-F17.
"""

import pytest

from creative_agent.errors import (
    BudgetExceededError,
    ConfigError,
    CreativeAgentError,
    ExitCode,
    LLMOutputError,
    LLMTimeoutError,
    LLMTransportError,
    OracleValidationError,
    ReviewFailedError,
    RunAbortedError,
    StateConflictError,
    StateCorruptError,
)


@pytest.mark.parametrize(
    ("exc_type", "code"),
    [
        (ConfigError, ExitCode.CONFIG_ERROR),
        (OracleValidationError, ExitCode.CONFIG_ERROR),
        (StateCorruptError, ExitCode.CONFIG_ERROR),
        (LLMTransportError, ExitCode.UNEXPECTED_ERROR),
        (LLMOutputError, ExitCode.REVIEW_FAILED),
        (ReviewFailedError, ExitCode.REVIEW_FAILED),
        (RunAbortedError, ExitCode.RUN_ABORTED),
        (BudgetExceededError, ExitCode.RUN_ABORTED),
        (LLMTimeoutError, ExitCode.RUN_ABORTED),
        (StateConflictError, ExitCode.RUN_ABORTED),
        (CreativeAgentError, ExitCode.UNEXPECTED_ERROR),
    ],
)
def test_exit_code_contract(exc_type: type[CreativeAgentError], code: ExitCode) -> None:
    assert exc_type.exit_code is code


def test_exit_code_values_are_frozen() -> None:
    assert [c.value for c in ExitCode] == [0, 1, 2, 3, 4, 5, 6]


def test_all_errors_are_creative_agent_errors() -> None:
    for exc_type in (
        ConfigError,
        OracleValidationError,
        StateCorruptError,
        ReviewFailedError,
        RunAbortedError,
        BudgetExceededError,
        LLMTimeoutError,
        StateConflictError,
    ):
        assert issubclass(exc_type, CreativeAgentError)


def test_abort_is_distinct_from_review_failure() -> None:
    """A run that was cut short must not look like a verdict about the artifact.

    REVIEW_FAILED says the review ran and its verification log could not be completed —
    a statement about the document. RUN_ABORTED says the run stopped before producing a
    verdict and nothing was published, so a CI consumer can retry. Collapsing the two
    would tell that consumer to treat a transient budget stop as a finding (DEC-F17).
    """
    assert RunAbortedError.exit_code is not ReviewFailedError.exit_code
    assert not issubclass(RunAbortedError, ReviewFailedError)
    assert not issubclass(ReviewFailedError, RunAbortedError)


def test_state_conflict_is_an_abort_not_a_config_error() -> None:
    """A concurrent writer is not operator misconfiguration.

    StateCorruptError is a ConfigError with `--reset-state` as its escape hatch. A
    conflict needs no operator action at all — re-running the review is the fix — so it
    must not be routed to the same exit code (DEC-F14).
    """
    assert issubclass(StateConflictError, RunAbortedError)
    assert not issubclass(StateConflictError, ConfigError)
    assert StateConflictError.exit_code is not StateCorruptError.exit_code
