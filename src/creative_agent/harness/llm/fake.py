"""FakeLLMClient: scripted, strict, keyed by call kind (never a blind ordered queue).

Strictness is the point (SQE review): a fake that accepts anything hides assembler
regressions, so every request is validated and unconsumed scripts fail the test.
"""

from __future__ import annotations

from creative_agent.errors import LLMTransportError
from creative_agent.harness.llm.base import AssembledPrompt, CallKind, RawLLMResult

WILDCARD_REF = "*"


def script_key(kind: CallKind, ref: str = "") -> str:
    return f"{kind.value}:{ref}"


class FakeLLMClient:
    """Pops scripted RawLLMResults (or raises scripted exceptions) per call key."""

    def __init__(self, scripts: dict[str, list[RawLLMResult | Exception]]) -> None:
        self._scripts = {key: list(items) for key, items in scripts.items()}
        self.calls: list[AssembledPrompt] = []

    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        self._validate(prompt)
        self.calls.append(prompt)
        for key in (script_key(prompt.kind, prompt.ref), script_key(prompt.kind, WILDCARD_REF)):
            queue = self._scripts.get(key)
            if queue:
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        raise LLMTransportError(
            f"FakeLLMClient has no scripted response for {script_key(prompt.kind, prompt.ref)!r}"
        )

    @staticmethod
    def _validate(prompt: AssembledPrompt) -> None:
        if not prompt.system.strip() or not prompt.user.strip():
            raise AssertionError("fake received an empty prompt")
        if not prompt.output_schema:
            raise AssertionError("fake received a call without an output schema")

    def assert_consumed(self) -> None:
        leftovers = {key: len(q) for key, q in self._scripts.items() if q}
        if leftovers:
            raise AssertionError(f"unconsumed scripted responses: {leftovers}")
