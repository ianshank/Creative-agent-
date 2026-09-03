"""The run-level spend cap (DEC-F41, extracting DEC-F17).

Extracted from `ReviewPipeline` because the property that matters here is arithmetic, and
arithmetic deserves a table rather than an end-to-end run against a scripted fake.

The defect this guards is worth restating, because it was a factor of a hundred. The SDK
applies `max_budget_usd` **per call**, and the harness passed the setting straight through
while summing nothing, so the effective cap was the setting multiplied by the number of
calls a review makes: 18 on the happy path, and up to roughly 144 once the classify
re-probe, the repair loop and the schema-retry loop compound. A $2.50 setting permitted
about $360.

Two rules, and they are different rules:

- **The pre-call check** refuses to start a call once spend has reached the budget. It
  cannot know what the next call will cost, so it bounds the overshoot at one call's price
  rather than eliminating it.
- **The remaining budget** is handed to the backend as *its* per-call ceiling, which is
  what turns "one call's price" into "at most the budget". Passing the raw setting instead
  made one number mean "per call" to the SDK and "per run" here, for a practical bound of
  roughly twice the setting.

Both were previously asserted by watching `prompt.remaining_budget_usd` decrease across a
pipeline run — a value computed by the statement *next to* the check, so hoisting the check
out of the retry loop left every test green.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from creative_agent.errors import BudgetExceededError
from creative_agent.harness.logging import get_logger, log_event

_LOG = get_logger(__name__)


class RunBudget:
    """Accumulates spend across every provider call in one review.

    `None` means uncapped, which is the default: a cap that a deployment did not ask for
    would abort long reviews for a reason the operator never chose.
    """

    def __init__(self, max_budget_usd: float | None) -> None:
        self._budget = max_budget_usd
        self._spent = 0.0
        self._calls = 0

    def record(self, cost_usd: object) -> None:
        """Add one call's cost, ignoring a backend that reports none.

        `OfflineLLMClient` and `FakeLLMClient` both leave `cost_usd` at None by design, and
        a run against them must not abort — a missing price is "this backend does not
        charge", not "this call was infinitely expensive".
        """
        self._calls += 1
        if isinstance(cost_usd, int | float) and not isinstance(cost_usd, bool):
            self._spent += float(cost_usd)

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def calls(self) -> int:
        return self._calls

    def remaining(self) -> float | None:
        """What is left, or None when uncapped. Never negative.

        Clamped at zero because this value becomes the backend's own per-call ceiling, and
        a negative ceiling is not a stricter limit — it is an argument the SDK may reject
        or, worse, ignore.
        """
        if self._budget is None:
            return None
        return max(self._budget - self._spent, 0.0)

    def check(self, *, kind: str, ref: str) -> None:
        """Refuse to start another call once the budget is reached.

        Called before *every* attempt, including each schema-repair retry, because each
        attempt reaches the wire. `>=` rather than `>`: at exactly the budget there is
        nothing left to spend.
        """
        if self._budget is None or self._spent < self._budget:
            return
        log_event(
            _LOG,
            logging.ERROR,
            "llm.budget_exhausted",
            kind=kind,
            ref=ref,
            spent_usd=round(self._spent, 6),
            budget_usd=self._budget,
            calls=self._calls,
        )
        raise BudgetExceededError(
            f"review budget of ${self._budget:.2f} exhausted after {self._calls} LLM calls "
            f"(${self._spent:.4f} spent); stopped before the {kind} call. Nothing was "
            "published. Raise max_budget_usd or narrow the review."
        )

    @classmethod
    def from_calls(
        cls, max_budget_usd: float | None, calls: Iterable[dict[str, object]]
    ) -> RunBudget:
        """Rebuild from an audit record, for a caller holding call dicts rather than a budget."""
        budget = cls(max_budget_usd)
        for call in calls:
            budget.record(call.get("cost_usd"))
        return budget
