"""AgentRegistry (DEC-F1): explicit registration.

An entry-point loader can be layered on when a second distribution actually exists;
until then explicit registration keeps discovery deterministic and test-friendly.
"""

from __future__ import annotations

from collections.abc import Callable

from creative_agent.errors import ConfigError
from creative_agent.harness.protocols import ReviewAgent

AgentFactory = Callable[[], ReviewAgent]


class AgentRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AgentFactory] = {}

    def register(self, name: str, factory: AgentFactory) -> None:
        if name in self._factories:
            raise ConfigError(f"agent {name!r} is already registered")
        self._factories[name] = factory

    def get(self, name: str) -> ReviewAgent:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise ConfigError(
                f"unknown agent {name!r}; available: {self.names() or 'none'}"
            ) from exc
        return factory()

    def names(self) -> list[str]:
        return sorted(self._factories)
