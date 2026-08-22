"""sutton-review: the first plugin agent.

Deliberately thin — the doctrine, severities, gates, markers, and traps all live in the
sutton oracle data file, and enforcement lives in the harness. This class only names its
oracle, its template directory (falls back to the packaged defaults), and extra prompt
context.
"""

from __future__ import annotations

from creative_agent.models.oracle import OracleTable
from creative_agent.models.review import ReviewRequest
from creative_agent.models.state import ReviewState


class SuttonReviewAgent:
    name = "sutton-review"

    def default_oracle(self) -> str:
        return "sutton"

    def prompt_template_dir(self) -> str:
        return "sutton"

    def build_context(
        self, request: ReviewRequest, oracle: OracleTable, state: ReviewState
    ) -> dict[str, object]:
        return {
            "usage_gates": oracle.protocol.usage_gates,
            "review_scope": "designs, not diffs",
        }
