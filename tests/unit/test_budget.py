"""The run budget as arithmetic (DEC-F41).

The defect this guards was a factor of a hundred: the SDK applies `max_budget_usd` per
call, the harness passed the setting straight through and summed nothing, so a $2.50
setting permitted about $360 across a review's ~144 possible provider calls.

It was previously asserted by watching `prompt.remaining_budget_usd` decrease across a
full pipeline run — a value computed by the statement *next to* the check — so hoisting
`_check_budget` out of the retry loop left every test green. A property about numbers
should be tested as numbers.
"""

from __future__ import annotations

import logging

import pytest

from creative_agent.errors import BudgetExceededError
from creative_agent.harness.budget import RunBudget


class TestAccumulation:
    def test_costs_sum_across_calls(self) -> None:
        budget = RunBudget(10.0)
        for cost in (1.0, 2.5, 0.25):
            budget.record(cost)
        assert budget.spent == pytest.approx(3.75)
        assert budget.calls == 3

    def test_a_backend_that_reports_no_cost_contributes_nothing(self) -> None:
        """`OfflineLLMClient` and `FakeLLMClient` leave `cost_usd` None by design.

        A missing price means "this backend does not charge", never "this call was
        infinitely expensive" — treating it as the latter aborts every offline run.
        """
        budget = RunBudget(1.0)
        for _ in range(100):
            budget.record(None)
        assert budget.spent == 0.0
        assert budget.calls == 100
        budget.check(kind="row", ref="D1")

    @pytest.mark.parametrize("junk", ["1.00", {"usd": 1}, [1], True, False])
    def test_a_non_numeric_cost_is_ignored_rather_than_crashing(self, junk: object) -> None:
        """A backend returning a string price must not end the review with a TypeError.

        `True` is in this list deliberately: `isinstance(True, int)` is True in Python, so
        a boolean would otherwise silently count as one dollar.
        """
        budget = RunBudget(1.0)
        budget.record(junk)
        assert budget.spent == 0.0


class TestTheCap:
    def test_an_uncapped_run_never_aborts(self) -> None:
        budget = RunBudget(None)
        budget.record(1_000_000.0)
        assert budget.remaining() is None
        budget.check(kind="row", ref="D1")

    @pytest.mark.parametrize(
        ("cap", "costs", "permitted"),
        [
            # Each call is checked BEFORE it runs, so spend can reach the cap and stop.
            (2.0, [1.0, 1.0, 1.0], 2),
            (2.0, [1.9, 1.9, 1.9], 2),
            (2.0, [0.5, 0.5, 0.5, 0.5, 0.5], 4),
            (0.0, [1.0], 0),
            (1.0, [1.0, 1.0], 1),
        ],
    )
    def test_how_many_calls_a_budget_permits(
        self, cap: float, costs: list[float], permitted: int
    ) -> None:
        """The table the old monotonicity assertion could not express."""
        budget = RunBudget(cap)
        made = 0
        for cost in costs:
            try:
                budget.check(kind="row", ref="D1")
            except BudgetExceededError:
                break
            budget.record(cost)
            made += 1
        assert made == permitted

    def test_the_overshoot_is_bounded_by_one_calls_price(self) -> None:
        """The honest statement of the guarantee.

        A pre-call check cannot know what the next call will cost, so it cannot eliminate
        overshoot — it bounds it at one call. Eliminating it is the *backend's* per-call
        ceiling's job, which is what `remaining()` is handed to the SDK for. DEC-F17
        originally claimed the stronger property and DEC-F19 had to correct it.
        """
        budget = RunBudget(2.0)
        while True:
            try:
                budget.check(kind="row", ref="D1")
            except BudgetExceededError:
                break
            budget.record(1.9)
        assert 2.0 <= budget.spent <= 2.0 + 1.9

    def test_exactly_at_the_budget_stops(self) -> None:
        """`>=`, not `>`: at exactly the cap there is nothing left to spend."""
        budget = RunBudget(1.0)
        budget.record(1.0)
        with pytest.raises(BudgetExceededError, match="exhausted"):
            budget.check(kind="synthesis", ref="")

    def test_the_abort_names_what_a_reader_needs(self) -> None:
        budget = RunBudget(1.0)
        budget.record(1.5)
        with pytest.raises(BudgetExceededError) as caught:
            budget.check(kind="row", ref="D1")
        message = str(caught.value)
        assert "$1.00" in message and "1 LLM calls" in message and "row" in message
        assert "Nothing was" in message, "an operator must know no report was published"


class TestTheRemainingBudgetHandedToTheBackend:
    def test_it_is_what_is_left(self) -> None:
        budget = RunBudget(5.0)
        budget.record(2.0)
        assert budget.remaining() == pytest.approx(3.0)

    def test_it_never_goes_negative(self) -> None:
        """This value becomes the SDK's own per-call ceiling, and a negative ceiling is not
        a stricter limit — it is an argument the SDK may reject or, worse, ignore."""
        budget = RunBudget(1.0)
        budget.record(5.0)
        assert budget.remaining() == 0.0

    def test_uncapped_means_none_not_zero(self) -> None:
        """Zero would tell the backend to spend nothing, which is the opposite of uncapped."""
        assert RunBudget(None).remaining() is None


class TestObservability:
    def test_exhaustion_is_logged_with_the_numbers(self, caplog: pytest.LogCaptureFixture) -> None:
        budget = RunBudget(1.0)
        budget.record(1.5)
        with (
            caplog.at_level(logging.ERROR, logger="creative_agent.harness.budget"),
            pytest.raises(BudgetExceededError),
        ):
            budget.check(kind="row", ref="D1")
        assert any("budget_exhausted" in r.getMessage() for r in caplog.records)


class TestRebuildingFromAnAuditRecord:
    def test_from_calls_sums_the_recorded_costs(self) -> None:
        calls: list[dict[str, object]] = [
            {"kind": "row", "cost_usd": 1.0},
            {"kind": "row", "cost_usd": None},
            {"kind": "row", "cost_usd": 0.5},
        ]
        budget = RunBudget.from_calls(3.0, calls)
        assert budget.spent == pytest.approx(1.5)
        assert budget.calls == 3
