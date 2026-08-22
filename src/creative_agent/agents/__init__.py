"""Plugin agents. The harness never imports this package (import-linter contract);
the CLI composition root builds the registry here."""

from __future__ import annotations

from creative_agent.agents.sutton_review import SuttonReviewAgent
from creative_agent.harness.registry import AgentRegistry


def build_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(SuttonReviewAgent.name, SuttonReviewAgent)
    return registry
