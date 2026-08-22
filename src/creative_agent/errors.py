"""Exception hierarchy, mapped 1:1 to CLI exit codes.

The mapping is a frozen contract (tested by the exit-code table test): CI consumers depend
on these codes, so adding a code is a visible, versioned change — see docs/decision-log.md.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """CLI exit codes. 0/1/2 encode review outcomes, 3-5 encode failures."""

    CLEAN = 0
    FINDINGS_MAJOR = 1
    BLOCKER_OR_STOP = 2
    REVIEW_FAILED = 3
    CONFIG_ERROR = 4
    UNEXPECTED_ERROR = 5


class CreativeAgentError(Exception):
    """Base for all typed harness errors."""

    exit_code: ExitCode = ExitCode.UNEXPECTED_ERROR


class ConfigError(CreativeAgentError):
    """Invalid settings, missing paths, unknown oracle/agent names."""

    exit_code = ExitCode.CONFIG_ERROR


class OracleValidationError(ConfigError):
    """An oracle data file failed schema validation. Message names file and key."""


class StateCorruptError(ConfigError):
    """A review-state file exists but its front matter cannot be parsed.

    Never silently resets the cycle counter; `--reset-state` is the explicit escape hatch.
    """


class LLMTransportError(CreativeAgentError):
    """The LLM backend failed at the transport level (auth, network, budget exhaustion)."""

    exit_code = ExitCode.UNEXPECTED_ERROR


class LLMOutputError(CreativeAgentError):
    """The LLM could not produce schema-valid structured output within the retry budget."""

    exit_code = ExitCode.REVIEW_FAILED


class ReviewFailedError(CreativeAgentError):
    """The review is failed per spec (e.g. incomplete verification log after repair).

    Per the sutton-review hard rule this is a refusal to publish, never a softening.
    """

    exit_code = ExitCode.REVIEW_FAILED
