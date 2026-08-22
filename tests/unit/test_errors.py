"""The exception→exit-code mapping is a frozen contract (README documents it)."""

import pytest

from creative_agent.errors import (
    ConfigError,
    CreativeAgentError,
    ExitCode,
    LLMOutputError,
    LLMTransportError,
    OracleValidationError,
    ReviewFailedError,
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
        (CreativeAgentError, ExitCode.UNEXPECTED_ERROR),
    ],
)
def test_exit_code_contract(exc_type: type[CreativeAgentError], code: ExitCode) -> None:
    assert exc_type.exit_code is code


def test_exit_code_values_are_frozen() -> None:
    assert [c.value for c in ExitCode] == [0, 1, 2, 3, 4, 5]


def test_all_errors_are_creative_agent_errors() -> None:
    for exc_type in (ConfigError, OracleValidationError, StateCorruptError, ReviewFailedError):
        assert issubclass(exc_type, CreativeAgentError)
