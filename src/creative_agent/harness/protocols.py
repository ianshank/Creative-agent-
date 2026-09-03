"""Structural protocols — the seams every harness component is injected through."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from creative_agent.harness.llm.base import AssembledPrompt, RawLLMResult
    from creative_agent.models.oracle import OracleTable, SourceRef
    from creative_agent.models.review import ReviewRequest
    from creative_agent.models.state import ReviewState


@runtime_checkable
class Clock(Protocol):
    """Injected time source. All harness time is aware-UTC (DEC-F8)."""

    def now(self) -> datetime:
        """Return the current instant as an aware-UTC datetime."""
        ...


@runtime_checkable
class LLMClient(Protocol):
    """A backend that answers one typed, schema-constrained call."""

    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        """Run one structured-output call and return the raw result + tool evidence."""
        ...


@runtime_checkable
class StateStore(Protocol):
    """Durable per-artifact review state."""

    def load(self, artifact_id: str) -> ReviewState: ...

    def save(
        self,
        state: ReviewState,
        summary_markdown: str = "",
        *,
        expected_cycle: int | None = None,
    ) -> Path:
        """Persist state, optionally refusing a lost update (DEC-F14).

        `expected_cycle` is the cycle the caller loaded. An implementation that supports
        it must re-read under the same lock that guards the write and raise
        `StateConflictError` on a mismatch. The keyword is optional so a store that cannot
        detect conflicts stays substitutable; the pipeline always passes it.
        """
        ...


@runtime_checkable
class CitationResolver(Protocol):
    """Resolves a SourceRef against the outside world (used by `oracles rebaseline`)."""

    async def resolve(self, ref: SourceRef) -> ResolutionResult: ...


class ResolutionResult:
    """Outcome of resolving one SourceRef."""

    __slots__ = ("detail", "resolved_authors", "resolved_title", "status")

    def __init__(
        self,
        status: str,
        resolved_authors: list[str] | None = None,
        resolved_title: str | None = None,
        detail: str = "",
    ) -> None:
        if status not in {"resolved", "mismatch", "unreachable", "skipped"}:
            raise ValueError(f"unknown resolution status: {status}")
        self.status = status
        self.resolved_authors = resolved_authors or []
        self.resolved_title = resolved_title
        self.detail = detail


@runtime_checkable
class ReviewAgent(Protocol):
    """A plugin agent: supplies prompts and its default oracle; never enforcement."""

    name: str

    def default_oracle(self) -> str: ...

    def prompt_template_dir(self) -> str:
        """Template directory name under the prompt search paths (e.g. "sutton")."""
        ...

    def build_context(
        self, request: ReviewRequest, oracle: OracleTable, state: ReviewState
    ) -> dict[str, object]:
        """Agent-specific template variables merged into every prompt render."""
        ...
