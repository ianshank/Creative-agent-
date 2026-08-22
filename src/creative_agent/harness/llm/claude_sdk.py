"""ClaudeSDKAdapter — the only module that touches the Claude Agent SDK.

Uses native structured output (ClaudeAgentOptions.output_format, DEC-F5) and headless
permissions (permission_mode from settings + restrictive allowed_tools — never
bypassPermissions). Message handling dispatches on type names so the adapter can be
exercised against recorded/mocked transports without importing SDK classes; the weekly
live workflow guards against surface drift.

A `PreToolUse` hook enforces DEC-F9's WebFetch scoping at the call boundary (DEC-F11a):
the allowlist was previously advisory-only (stated in the prompt, never enforced). The
decision logic itself (`is_fetch_allowed`) lives in `harness/security.py`, which is
coverage-counted — this module stays thin glue only, since it is the one file DEC-F8
excludes from the coverage gate and a check written inline here could ship untested.

Excluded from the coverage gate (visible omit, DEC-F8): exercised by mocked-transport
tests here and by `pytest -m live` / scripts/sdk_spike.py against the real SDK.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from creative_agent.config import HarnessSettings
from creative_agent.errors import LLMOutputError, LLMTransportError
from creative_agent.harness.llm.base import AssembledPrompt, RawLLMResult, ToolEvidence
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.harness.security import is_fetch_allowed

_LOG = get_logger(__name__)
_TARGET_KEYS = ("url", "file_path", "path", "pattern", "query")


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _pre_tool_use_hook(prompt: AssembledPrompt) -> Any:
    """Builds a PreToolUse hook closed over this call's computed fetch allowlist.

    Checked against the allowlist actually computed for this review (DEC-F11a) — not a
    fresh internal-host check, which would permit any public host rather than only the
    oracle- and artifact-derived set the model was told about in the system prompt.
    """

    async def hook(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "WebFetch":
            return {}
        url = str(input_data.get("tool_input", {}).get("url", ""))
        if is_fetch_allowed(url, prompt.fetch_domain_allowlist):
            return {}
        log_event(
            _LOG,
            logging.WARNING,
            "security.pretooluse_denied",
            call_kind=prompt.kind.value,
            tool_name="WebFetch",
        )
        return _deny("WebFetch host is not in this review's computed allowlist")

    return hook


def _target_from_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in _TARGET_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
    return str(tool_input)


class ClaudeSDKAdapter:
    """LLMClient backed by claude_agent_sdk.query()."""

    def __init__(self, settings: HarnessSettings) -> None:
        self._settings = settings

    def _options(self, prompt: AssembledPrompt) -> Any:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        except ImportError as exc:
            raise LLMTransportError(
                "claude-agent-sdk is not installed; install with the [llm] extra"
            ) from exc
        kwargs: dict[str, Any] = {
            "system_prompt": prompt.system,
            "allowed_tools": prompt.allowed_tools,
            "permission_mode": self._settings.permission_mode,
            "max_turns": self._settings.max_turns,
            "output_format": {"type": "json_schema", "schema": prompt.output_schema},
            "hooks": {
                "PreToolUse": [HookMatcher(matcher="WebFetch", hooks=[_pre_tool_use_hook(prompt)])]
            },
        }
        if self._settings.model != "inherit":
            kwargs["model"] = self._settings.model
        if self._settings.fallback_model:
            kwargs["fallback_model"] = self._settings.fallback_model
        if self._settings.max_budget_usd is not None:
            kwargs["max_budget_usd"] = self._settings.max_budget_usd
        return ClaudeAgentOptions(**kwargs)

    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        try:
            from claude_agent_sdk import query
        except ImportError as exc:
            raise LLMTransportError(
                "claude-agent-sdk is not installed; install with the [llm] extra"
            ) from exc
        return await self._run(query, prompt)

    async def _run(self, query: Any, prompt: AssembledPrompt) -> RawLLMResult:
        options = self._options(prompt)
        pending_tool_uses: dict[str, tuple[str, str]] = {}
        evidence: list[ToolEvidence] = []
        structured: Any = None
        result_text: str | None = None
        cost: float | None = None
        model = ""
        subtype: str | None = None

        async for message in query(prompt=prompt.user, options=options):
            message_kind = type(message).__name__
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    block_kind = type(block).__name__
                    if block_kind == "ToolUseBlock":
                        pending_tool_uses[block.id] = (
                            block.name,
                            _target_from_input(block.input),
                        )
                    elif block_kind == "ToolResultBlock":
                        used = pending_tool_uses.get(block.tool_use_id)
                        if used is not None:
                            evidence.append(
                                ToolEvidence(
                                    tool_name=used[0],
                                    target=used[1],
                                    ok=not bool(getattr(block, "is_error", False)),
                                )
                            )
            if message_kind == "ResultMessage":
                subtype = getattr(message, "subtype", None)
                structured = getattr(message, "structured_output", None)
                result_text = getattr(message, "result", None)
                cost = getattr(message, "total_cost_usd", None)
                model = getattr(message, "model", "") or ""

        if subtype == "error_max_structured_output_retries":
            raise LLMOutputError(
                f"{prompt.kind.value} call exhausted the SDK's structured-output retries"
            )
        payload = self._extract_payload(prompt, structured, result_text)
        return RawLLMResult(
            kind=prompt.kind,
            payload=payload,
            tool_evidence=evidence,
            model=model,
            cost_usd=cost,
        )

    @staticmethod
    def _extract_payload(
        prompt: AssembledPrompt, structured: Any, result_text: str | None
    ) -> dict[str, Any]:
        if isinstance(structured, dict):
            return structured
        if result_text:
            try:
                parsed = json.loads(result_text)
            except json.JSONDecodeError as exc:
                raise LLMOutputError(
                    f"{prompt.kind.value} call returned no structured output and "
                    "non-JSON result text"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise LLMOutputError(f"{prompt.kind.value} call produced no structured output")
