"""ClaudeSDKAdapter against a mocked transport (type-name-shaped like the real SDK).

The shapes here mirror the documented SDK surface (AssistantMessage/ToolUseBlock/
ToolResultBlock/ResultMessage); the weekly live workflow re-validates them against the
real SDK so this mock cannot silently fossilize.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from creative_agent.config import HarnessSettings
from creative_agent.errors import LLMOutputError
from creative_agent.harness.llm.base import AssembledPrompt, CallKind
from creative_agent.harness.llm.claude_sdk import ClaudeSDKAdapter, _target_from_input


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list[Any] = field(default_factory=list)


@dataclass
class UserMessage:
    content: list[Any] = field(default_factory=list)


@dataclass
class ResultMessage:
    subtype: str = "success"
    structured_output: Any = None
    result: str | None = None
    total_cost_usd: float | None = 0.05
    model: str = "test-model"


def transport(messages: list[Any]) -> Any:
    captured: dict[str, Any] = {}

    async def query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        captured["prompt"] = prompt
        captured["options"] = options
        for message in messages:
            yield message

    query.captured = captured  # type: ignore[attr-defined]
    return query


def prompt(kind: CallKind = CallKind.CLASSIFY) -> AssembledPrompt:
    return AssembledPrompt(
        kind=kind,
        system="system text",
        user="user text",
        output_schema={"type": "object"},
        allowed_tools=["Read", "WebFetch"],
    )


def adapter(**settings_overrides: Any) -> ClaudeSDKAdapter:
    return ClaudeSDKAdapter(HarnessSettings(**settings_overrides))


class TestGenerate:
    async def test_structured_output_and_cost_extracted(self) -> None:
        query = transport(
            [ResultMessage(structured_output={"artifact_class": "architecture_design"})]
        )
        result = await adapter()._run(query, prompt())
        assert result.payload == {"artifact_class": "architecture_design"}
        assert result.cost_usd == 0.05
        assert result.model == "test-model"

    async def test_tool_evidence_matched_by_use_id(self) -> None:
        query = transport(
            [
                AssistantMessage(
                    [ToolUseBlock("t1", "WebFetch", {"url": "https://arxiv.org/abs/1"})]
                ),
                UserMessage([ToolResultBlock("t1", is_error=False)]),
                AssistantMessage(
                    [ToolUseBlock("t2", "WebFetch", {"url": "https://arxiv.org/abs/2"})]
                ),
                UserMessage([ToolResultBlock("t2", is_error=True)]),
                ResultMessage(structured_output={}),
            ]
        )
        result = await adapter()._run(query, prompt())
        assert [(e.target, e.ok) for e in result.tool_evidence] == [
            ("https://arxiv.org/abs/1", True),
            ("https://arxiv.org/abs/2", False),
        ]

    async def test_structured_output_retry_exhaustion_is_typed(self) -> None:
        query = transport([ResultMessage(subtype="error_max_structured_output_retries")])
        with pytest.raises(LLMOutputError, match="structured-output retries"):
            await adapter()._run(query, prompt())

    async def test_json_result_text_fallback(self) -> None:
        query = transport([ResultMessage(result='{"claims": []}')])
        result = await adapter()._run(query, prompt(CallKind.CLAIMS))
        assert result.payload == {"claims": []}

    async def test_no_output_is_typed_error(self) -> None:
        query = transport([ResultMessage(result="not json")])
        with pytest.raises(LLMOutputError):
            await adapter()._run(query, prompt())

    async def test_options_carry_settings_and_schema(self) -> None:
        pytest.importorskip("claude_agent_sdk")
        query = transport([ResultMessage(structured_output={})])
        sdk_adapter = adapter(model="opus", max_turns=3, permission_mode="dontAsk")
        await sdk_adapter._run(query, prompt())
        options = query.captured["options"]
        assert options.model == "opus"
        assert options.permission_mode == "dontAsk"
        assert options.max_turns == 3
        assert options.output_format == {
            "type": "json_schema",
            "schema": {"type": "object"},
        }
        assert "bypass" not in str(options.permission_mode).lower()

    async def test_inherit_model_is_not_passed(self) -> None:
        pytest.importorskip("claude_agent_sdk")
        query = transport([ResultMessage(structured_output={})])
        await adapter(model="inherit")._run(query, prompt())
        assert query.captured["options"].model is None


class TestPreToolUseWebFetchEnforcement:
    """DEC-F11a: the fetch allowlist is enforced at the call boundary, not just stated in
    the prompt. The mocked transport doesn't run the SDK's hook machinery, so these invoke
    the wired hook function directly — same as the real SDK would."""

    @staticmethod
    def _wired_hook(assembled_prompt: AssembledPrompt) -> Any:
        pytest.importorskip("claude_agent_sdk")
        options = adapter()._options(assembled_prompt)
        (matcher,) = options.hooks["PreToolUse"]
        assert matcher.matcher == "WebFetch|Read|Grep|Glob"
        (hook,) = matcher.hooks
        return hook

    async def test_denies_a_host_outside_the_allowlist(self) -> None:
        hook = self._wired_hook(
            prompt().model_copy(update={"fetch_domain_allowlist": ["arxiv.org"]})
        )
        result = await hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://evil.example/x"}},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_allows_a_host_inside_the_allowlist(self) -> None:
        hook = self._wired_hook(
            prompt().model_copy(update={"fetch_domain_allowlist": ["arxiv.org"]})
        )
        result = await hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://arxiv.org/abs/1"}},
            "t1",
            None,
        )
        assert result == {}

    async def test_ignores_tools_outside_its_scope(self) -> None:
        """The hook only judges WebFetch/Read/Grep/Glob; other tools pass through."""
        hook = self._wired_hook(prompt().model_copy(update={"fetch_domain_allowlist": []}))
        result = await hook({"tool_name": "TodoWrite", "tool_input": {}}, "t1", None)
        assert result == {}

    async def test_empty_allowlist_denies_every_host(self) -> None:
        hook = self._wired_hook(prompt().model_copy(update={"fetch_domain_allowlist": []}))
        result = await hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://arxiv.org/abs/1"}},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestPreToolUseReadEnforcement:
    """DEC-F11b: Read/Grep/Glob path scoping enforced at the call boundary — previously
    computed by ThreatGuard.allowed_read_roots but never reaching the SDK or the prompt at
    all. Same wired-hook approach as the WebFetch tests: the mocked transport doesn't run
    the SDK's hook machinery, so these invoke the hook function directly."""

    @staticmethod
    def _wired_hook(assembled_prompt: AssembledPrompt) -> Any:
        pytest.importorskip("claude_agent_sdk")
        options = adapter()._options(assembled_prompt)
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        return hook

    async def test_denies_a_read_outside_the_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "artifact-dir"
        root.mkdir()
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": [root]}))
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "secret.env")}},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_allows_a_read_inside_the_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "artifact-dir"
        root.mkdir()
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": [root]}))
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(root / "doc.md")}},
            "t1",
            None,
        )
        assert result == {}

    async def test_glob_without_an_explicit_path_falls_back_to_cwd(self, tmp_path: Path) -> None:
        """Grep/Glob's `path` argument is optional; the SDK reports cwd when omitted."""
        root = tmp_path / "artifact-dir"
        root.mkdir()
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": [root]}))
        allowed = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": "*.md"}, "cwd": str(root)},
            "t1",
            None,
        )
        assert allowed == {}
        denied = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": "*.md"}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_grep_with_an_explicit_path_uses_it_over_cwd(self, tmp_path: Path) -> None:
        root = tmp_path / "artifact-dir"
        root.mkdir()
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": [root]}))
        result = await hook(
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": "TODO", "path": str(tmp_path / "elsewhere")},
                "cwd": str(root),
            },
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_empty_roots_denies_every_read(self, tmp_path: Path) -> None:
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": []}))
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "doc.md")}},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestTargetExtraction:
    @pytest.mark.parametrize(
        ("tool_input", "expected"),
        [
            ({"url": "https://x"}, "https://x"),
            ({"file_path": "/a/b.md"}, "/a/b.md"),
            ({"pattern": "gamma"}, "gamma"),
            ({"weird": 1}, "{'weird': 1}"),
        ],
    )
    def test_targets(self, tool_input: dict[str, Any], expected: str) -> None:
        assert _target_from_input(tool_input) == expected
