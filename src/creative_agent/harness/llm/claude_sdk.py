"""ClaudeSDKAdapter — the only module that touches the Claude Agent SDK.

Uses native structured output (ClaudeAgentOptions.output_format, DEC-F5) and headless
permissions (permission_mode from settings + restrictive allowed_tools — never
bypassPermissions). Message handling dispatches on type names so the adapter can be
exercised against recorded/mocked transports without importing SDK classes; the weekly
live workflow guards against surface drift.

A `PreToolUse` hook enforces DEC-F9's scoping at the call boundary: WebFetch host allowlist
(DEC-F11a) and Read/Grep/Glob path scoping (DEC-F11b), both previously advisory-only or
fully disconnected. The decision logic itself (`is_fetch_allowed`, `is_path_within_roots`)
lives in `harness/security.py`, which is coverage-counted — this module stays thin glue
only, since it is the one file DEC-F8 excludes from the coverage gate and a check written
inline here could ship untested.

Excluded from the coverage gate (visible omit, DEC-F8): exercised by mocked-transport
tests here and by `pytest -m live` / scripts/sdk_spike.py against the real SDK.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creative_agent.config import HarnessSettings
from creative_agent.errors import LLMOutputError, LLMTransportError
from creative_agent.harness.llm.base import AssembledPrompt, RawLLMResult, ToolEvidence
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.harness.security import (
    is_fetch_allowed,
    is_glob_within_roots,
    is_path_within_roots,
)

_LOG = get_logger(__name__)
_TARGET_KEYS = ("url", "file_path", "path", "pattern", "query")


# Per-tool argument shapes. Keeping these as data rather than an `in (...)` chain is what
# makes the Grep/Glob distinction survivable: Glob's `pattern` is a *path* glob, while
# Grep's `pattern` is a regular expression and its `glob` is the path filter. Treating
# Grep's regex as a path would deny legitimate searches; ignoring Glob's pattern let
# `/etc/**/*` through (DEC-F15).
@dataclass(frozen=True)
class _ToolScope:
    """How one path-bearing tool's arguments map onto the read-root check."""

    path_keys: tuple[str, ...]
    # Read's file_path is required, so its absence is a malformed call. Grep's and Glob's
    # path is genuinely optional and defaults to the session working directory.
    path_required: bool
    # Path-shaped glob arguments. Glob's `pattern` is a path glob; Grep's `pattern` is a
    # regular expression and only its `glob` filters paths. Scoping Grep's regex would
    # deny a legitimate search for a path-looking string inside the artifact.
    glob_keys: tuple[str, ...] = ()


_TOOL_SCOPES: dict[str, _ToolScope] = {
    "Read": _ToolScope(path_keys=("file_path",), path_required=True),
    "Grep": _ToolScope(path_keys=("path",), path_required=False, glob_keys=("glob",)),
    "Glob": _ToolScope(path_keys=("path",), path_required=False, glob_keys=("pattern",)),
}


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _path_scope_violation(
    tool_name: str, tool_input: dict[str, Any], cwd: str, roots: list[Path]
) -> str | None:
    """Why a path-scoped tool call is out of scope, or None when it is allowed.

    Split out of the hook so the decision reads as one linear rule per argument, and so
    the hook body stays the thin glue DEC-F11 requires of this module — the module is the
    one file DEC-F8 excludes from the coverage gate.
    """
    scope = _TOOL_SCOPES[tool_name]
    for key in scope.path_keys:
        if key not in tool_input:
            continue
        value = tool_input[key]
        # Present-but-empty is a malformed call, not a request for the cwd. The old
        # truthiness test silently degraded `{"file_path": ""}` to the cwd check.
        if not isinstance(value, str) or not value.strip():
            return f"{tool_name} {key} is empty or not a string"
        if not is_path_within_roots(value, roots, cwd=cwd):
            return f"{tool_name} {key} is outside this review's read roots"
        break
    else:
        if scope.path_required:
            return f"{tool_name} requires {scope.path_keys[0]} and the call supplied none"
        # An optional path is absent: the tool searches the session working directory.
        if not is_path_within_roots(cwd, roots, cwd=cwd):
            return (
                f"{tool_name} would search the session working directory, which is "
                "outside this review's read roots"
            )

    base = str(tool_input.get(scope.path_keys[0], "") or cwd)
    for key in scope.glob_keys:
        pattern = tool_input.get(key)
        if (
            isinstance(pattern, str)
            and pattern
            and not is_glob_within_roots(pattern, roots, cwd=base)
        ):
            return f"{tool_name} {key} pattern would search outside this review's read roots"
    return None


def _pre_tool_use_hook(prompt: AssembledPrompt, unscoped_tools: frozenset[str]) -> Any:
    """Builds a PreToolUse hook closed over this call's computed scopes.

    Deny-by-default (DEC-F15). A tool is allowed only if it is explicitly handled here or
    named in `HarnessSettings.unscoped_tools`; the previous version returned allow for
    anything it did not recognise, and its matcher only fired for four tool names, so
    nothing else was ever inspected.

    WebFetch (DEC-F11a) is checked against the exact allowlist computed for this review —
    not a fresh internal-host check, which would permit any public host rather than only
    the oracle- and artifact-derived set the model was told about in the system prompt.
    Read/Grep/Glob (DEC-F11b) are checked against the computed read roots, *including*
    their glob patterns: `Glob` takes a required `pattern` and an optional `path`, so with
    `path` absent the old check validated the session cwd and ignored where the pattern
    actually pointed. A relative path is resolved against the tool call's own reported
    `cwd`, not this process's — the two can differ, and resolving against the wrong one is
    a scoping bypass, not just a wrong answer.
    """

    def _refuse(tool_name: str, reason: str) -> dict[str, Any]:
        log_event(
            _LOG,
            logging.WARNING,
            "security.pretooluse_denied",
            call_kind=prompt.kind.value,
            tool_name=tool_name,
            reason=reason,
        )
        return _deny(reason)

    async def hook(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        tool_name = str(input_data.get("tool_name") or "")
        raw_input = input_data.get("tool_input")
        # A null or non-mapping tool_input used to raise inside the hook. Whether the SDK
        # treats a hook exception as allow or deny is not something to depend on, so a
        # malformed call is normalised to an empty mapping and then judged on its merits —
        # which, for a path-scoped tool, means denial for a missing target.
        tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}

        if tool_name == "WebFetch":
            url = str(tool_input.get("url", ""))
            if is_fetch_allowed(url, prompt.fetch_domain_allowlist):
                return {}
            return _refuse(
                tool_name,
                "WebFetch scheme must be http/https and the host must be in this "
                "review's computed allowlist",
            )

        if tool_name in _TOOL_SCOPES:
            reason = _path_scope_violation(
                tool_name, tool_input, str(input_data.get("cwd", "")), prompt.allowed_read_roots
            )
            return _refuse(tool_name, reason) if reason else {}

        if tool_name in unscoped_tools:
            if tool_name == "WebSearch":
                # DEC-F15's accepted residual risk, made observable. The query itself is
                # never logged — DEC-F10 forbids logging prompt or artifact text — but its
                # presence and size are, so an unexpected outbound channel is greppable.
                log_event(
                    _LOG,
                    logging.INFO,
                    "security.websearch_issued",
                    call_kind=prompt.kind.value,
                    query_chars=len(str(tool_input.get("query", ""))),
                )
            return {}

        return _refuse(
            tool_name or "<unnamed>",
            "tool is not scoped by this review's threat model and is not listed in unscoped_tools",
        )

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
            # No matcher: the hook must see *every* tool call, because it denies by
            # default (DEC-F15). A name-based matcher meant an unrecognised tool was never
            # inspected at all, which is the opposite of fail-closed.
            "hooks": {
                "PreToolUse": [
                    HookMatcher(
                        hooks=[_pre_tool_use_hook(prompt, frozenset(self._settings.unscoped_tools))]
                    )
                ]
            },
        }
        if self._settings.model != "inherit":
            kwargs["model"] = self._settings.model
        if self._settings.fallback_model:
            kwargs["fallback_model"] = self._settings.fallback_model
        # The SDK's max_budget_usd is per call. Passing the raw setting made the same
        # number mean "per call" here and "per run" in the pipeline, so the practical bound
        # was roughly twice the setting. The pipeline's remaining-run budget is the correct
        # per-call ceiling; the setting is only the fallback when no run context exists.
        per_call_budget = (
            prompt.remaining_budget_usd
            if prompt.remaining_budget_usd is not None
            else self._settings.max_budget_usd
        )
        if per_call_budget is not None:
            kwargs["max_budget_usd"] = max(per_call_budget, 0.0)
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
