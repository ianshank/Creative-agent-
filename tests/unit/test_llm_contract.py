"""Shared LLMClient contract suite (SQE review M1: fake and real must agree).

Every implementation must: return RawLLMResult with the call's kind, and surface
failures as typed CreativeAgentErrors — never KeyError from a fake and SDK-specific
exceptions from the adapter.
"""

from __future__ import annotations

from typing import Any

import pytest

from creative_agent.config import HarnessSettings
from creative_agent.errors import CreativeAgentError
from creative_agent.harness.llm.base import AssembledPrompt, CallKind, RawLLMResult
from creative_agent.harness.llm.claude_sdk import ClaudeSDKAdapter
from creative_agent.harness.llm.fake import FakeLLMClient, script_key
from creative_agent.harness.llm.offline import OfflineLLMClient
from creative_agent.harness.prompting import output_model_for
from creative_agent.harness.protocols import LLMClient
from tests.live_support import SKIP_REASON, sdk_credential_available
from tests.unit.test_claude_sdk_adapter import ResultMessage, transport


def make_prompt(kind: CallKind) -> AssembledPrompt:
    return AssembledPrompt(
        kind=kind,
        ref="D1" if kind is CallKind.ROW else "",
        system="s",
        user="u",
        output_schema=output_model_for(kind).model_json_schema(),
    )


class _AdapterHarness:
    """ClaudeSDKAdapter bound to a mocked transport that echoes a valid payload."""

    def __init__(self) -> None:
        self._adapter = ClaudeSDKAdapter(HarnessSettings())

    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        payload: dict[str, Any] = {
            CallKind.CLAIMS: {"claims": []},
            CallKind.SYNTHESIS: {
                "headline": "x",
                "confidence": "Likely",
                "what_survives": [],
                "residual_risks": [],
            },
        }.get(prompt.kind, {"claims": []})
        query = transport([ResultMessage(structured_output=payload)])
        return await self._adapter._run(query, prompt)


def implementations() -> list[tuple[str, LLMClient]]:
    fake = FakeLLMClient(
        {
            script_key(CallKind.CLAIMS): [
                RawLLMResult(kind=CallKind.CLAIMS, payload={"claims": []})
            ],
            script_key(CallKind.SYNTHESIS): [
                RawLLMResult(
                    kind=CallKind.SYNTHESIS,
                    payload={
                        "headline": "x",
                        "confidence": "Likely",
                        "what_survives": [],
                        "residual_risks": [],
                    },
                )
            ],
        }
    )
    return [
        ("fake", fake),
        ("offline", OfflineLLMClient()),
        ("sdk-mocked", _AdapterHarness()),
    ]


@pytest.mark.parametrize(("name", "client"), implementations(), ids=str)
class TestLLMClientContract:
    async def test_result_kind_matches_call(self, name: str, client: LLMClient) -> None:
        result = await client.generate(make_prompt(CallKind.CLAIMS))
        assert isinstance(result, RawLLMResult)
        assert result.kind is CallKind.CLAIMS

    async def test_payload_validates_against_call_schema(
        self, name: str, client: LLMClient
    ) -> None:
        prompt = make_prompt(CallKind.SYNTHESIS)
        result = await client.generate(prompt)
        output_model_for(CallKind.SYNTHESIS).model_validate(result.payload)


class TestContractErrors:
    async def test_fake_raises_typed_error_for_unknown_call(self) -> None:
        with pytest.raises(CreativeAgentError):
            await FakeLLMClient({}).generate(make_prompt(CallKind.CLASSIFY))

    async def test_adapter_raises_typed_error_for_bad_output(self) -> None:
        adapter = ClaudeSDKAdapter(HarnessSettings())
        query = transport([ResultMessage(result="not json")])
        with pytest.raises(CreativeAgentError):
            await adapter._run(query, make_prompt(CallKind.CLASSIFY))


@pytest.mark.live()
@pytest.mark.skipif(not sdk_credential_available(), reason=SKIP_REASON)
class TestLiveSmoke:
    """Weekly: real SDK calls (live.yml).

    Gated on a usable credential rather than on `ANTHROPIC_API_KEY` specifically — the
    SDK authenticates through the `claude` CLI too, and gating on the key alone skipped
    this leg in environments where a live call works (DEC-F21).
    """

    async def test_real_structured_output(self) -> None:
        adapter = ClaudeSDKAdapter(HarnessSettings(max_turns=2))
        prompt = AssembledPrompt(
            kind=CallKind.CLAIMS,
            system="Extract measurable claims. Reply only via structured output.",
            user="The system processes 100 requests per second on one core.",
            output_schema=output_model_for(CallKind.CLAIMS).model_json_schema(),
        )
        result = await adapter.generate(prompt)
        output_model_for(CallKind.CLAIMS).model_validate(result.payload)

    async def test_the_hook_does_not_deny_the_sdks_own_response_channel(self) -> None:
        """The live regression for DEC-F20, stated as its own test.

        `test_real_structured_output` above would also have caught it — the adapter builds
        the same hook — which is the point worth recording: the gate existed, aimed at
        exactly this, and never ran, because it skipped on a credential form the SDK does
        not require. This test names the defect so a future reader knows what the live leg
        is for, rather than inferring it from a smoke test.

        A default-settings adapter is used deliberately. The defect was in the defaults.
        """
        adapter = ClaudeSDKAdapter(HarnessSettings(max_turns=2))
        prompt = AssembledPrompt(
            kind=CallKind.CLAIMS,
            system="Extract measurable claims. Reply only via structured output.",
            user="The controller runs at 50 Hz on one core.",
            output_schema=output_model_for(CallKind.CLAIMS).model_json_schema(),
        )
        result = await adapter.generate(prompt)
        assert result.kind is CallKind.CLAIMS
        output_model_for(CallKind.CLAIMS).model_validate(result.payload)
