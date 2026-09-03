"""ClaudeSDKAdapter against a mocked transport (type-name-shaped like the real SDK).

The shapes here mirror the documented SDK surface (AssistantMessage/ToolUseBlock/
ToolResultBlock/ResultMessage); the weekly live workflow re-validates them against the
real SDK so this mock cannot silently fossilize.
"""

from __future__ import annotations

import builtins
import logging
import tomllib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import claude_agent_sdk
import pytest

from creative_agent.config import HarnessSettings
from creative_agent.errors import LLMOutputError, LLMTransportError
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
        query = transport([ResultMessage(structured_output={})])
        await adapter(model="inherit")._run(query, prompt())
        assert query.captured["options"].model is None


class TestPreToolUseWebFetchEnforcement:
    """DEC-F11a: the fetch allowlist is enforced at the call boundary, not just stated in
    the prompt. The mocked transport doesn't run the SDK's hook machinery, so these invoke
    the wired hook function directly — same as the real SDK would."""

    @staticmethod
    def _wired_hook(assembled_prompt: AssembledPrompt) -> Any:
        options = adapter()._options(assembled_prompt)
        (matcher,) = options.hooks["PreToolUse"]
        # No matcher at all: the hook denies by default (DEC-F15), so it has to see every
        # tool call. A name-based matcher meant an unrecognised tool was never inspected,
        # which is the opposite of fail-closed.
        assert matcher.matcher is None
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

    async def test_an_unrecognised_tool_is_denied_not_passed_through(self) -> None:
        """Deny-by-default (DEC-F15).

        This previously asserted the opposite — that a tool outside the four scoped names
        "passes through" — which enshrined the fail-open behaviour as intended. Anything
        the threat model does not scope and the operator has not listed in
        `unscoped_tools` is refused.
        """
        hook = self._wired_hook(prompt().model_copy(update={"fetch_domain_allowlist": []}))
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "id"}}, "t1", None)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

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

    async def test_relative_explicit_path_resolves_against_reported_cwd(
        self, tmp_path: Path
    ) -> None:
        """A relative `path` argument must resolve against the SDK tool call's own
        reported `cwd` — not this process's cwd. Getting this wrong is a scoping bypass:
        a relative path allowed here because it happened to resolve against wherever
        pytest itself was invoked from, rather than where the tool call actually ran."""
        root = tmp_path / "artifact-dir"
        root.mkdir()
        (root / "doc.md").write_text("x", encoding="utf-8")
        hook = self._wired_hook(prompt().model_copy(update={"allowed_read_roots": [root]}))
        result = await hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "doc.md"},
                "cwd": str(root),
            },
            "t1",
            None,
        )
        assert result == {}

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


class TestPreToolUseClosedHoles:
    """The three ways the DEC-F11 hook could still be walked past (DEC-F15).

    Each of these was allowed before: a `Glob` pattern was never looked at, an unnamed
    tool was never inspected, and a present-but-empty path silently degraded to the cwd
    check. They are grouped here because they share one root cause — the hook judged the
    call's *name* and one argument, rather than every argument that can carry a target.
    """

    @staticmethod
    def _hook(roots: list[Path]) -> Any:
        options = adapter()._options(prompt().model_copy(update={"allowed_read_roots": roots}))
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        return hook

    @pytest.mark.parametrize(
        "pattern",
        ["/etc/**/*", "/home/someone/**/*.pem", "../../**/*", "../outside/*.md"],
    )
    async def test_a_glob_pattern_that_escapes_the_roots_is_denied(
        self, tmp_path: Path, pattern: str
    ) -> None:
        """`Glob` takes a required pattern and an OPTIONAL path.

        With `path` absent the old check validated the session cwd — which is inside a
        root — and ignored the pattern entirely, so an absolute or traversing pattern
        enumerated whatever it liked. DEC-F11b claimed Glob was covered; it was not.
        """
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": pattern}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.parametrize("pattern", ["*.md", "**/*.py", "sub/*.txt"])
    async def test_a_relative_glob_pattern_inside_the_roots_is_allowed(
        self, tmp_path: Path, pattern: str
    ) -> None:
        """The fix must not break the searches a reviewer legitimately needs."""
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": pattern}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result == {}

    async def test_greps_regex_pattern_is_not_treated_as_a_path(self, tmp_path: Path) -> None:
        """Grep's `pattern` is a regular expression; only its `glob` filters paths.

        Scoping the regex would deny ordinary searches — a search for `/etc/passwd` as a
        *string in the artifact* is exactly what a reviewer should be able to run.
        """
        hook = self._hook([tmp_path])
        result = await hook(
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": "/etc/passwd", "path": str(tmp_path)},
                "cwd": str(tmp_path),
            },
            "t1",
            None,
        )
        assert result == {}

    async def test_greps_glob_filter_is_scoped(self, tmp_path: Path) -> None:
        hook = self._hook([tmp_path])
        result = await hook(
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": "secret", "glob": "/etc/**"},
                "cwd": str(tmp_path),
            },
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_an_empty_path_is_denied_rather_than_falling_back_to_cwd(
        self, tmp_path: Path
    ) -> None:
        """`tool_input.get(key)` was a truthiness test, so `{"file_path": ""}` passed."""
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": ""}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_a_null_tool_input_does_not_raise_inside_the_hook(self, tmp_path: Path) -> None:
        """Whether the SDK treats a hook exception as allow or deny is not ours to assume.

        A null `tool_input` used to raise an AttributeError inside the hook. It is now
        normalised to an empty mapping and judged on its merits, which for Read — whose
        `file_path` is required — means denial.
        """
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Read", "tool_input": None, "cwd": str(tmp_path)}, "t1", None
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_grep_without_a_path_falls_back_to_the_cwd_check(self, tmp_path: Path) -> None:
        """Grep's path is genuinely optional, unlike Read's file_path."""
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result == {}

    async def test_grep_without_a_path_is_denied_when_the_cwd_is_outside_the_roots(
        self, tmp_path: Path
    ) -> None:
        hook = self._hook([tmp_path / "allowed"])
        result = await hook(
            {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_websearch_is_allowed_because_settings_list_it_as_unscoped(
        self, tmp_path: Path
    ) -> None:
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "WebSearch", "tool_input": {"query": "reward hypothesis"}}, "t1", None
        )
        assert result == {}

    async def test_the_websearch_query_is_counted_but_never_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """DEC-F10 forbids logging prompt or artifact text; the outbound channel still
        has to be greppable, so the event carries the query's size and not the query."""
        hook = self._hook([tmp_path])
        secret = "a-very-distinctive-search-string"
        with caplog.at_level(logging.INFO, logger="creative_agent"):
            await hook({"tool_name": "WebSearch", "tool_input": {"query": secret}}, "t1", None)
        events = [
            getattr(r, "context", {})
            for r in caplog.records
            if r.getMessage() == "security.websearch_issued"
        ]
        assert events, "the outbound search was not recorded at all"
        assert events[0]["query_chars"] == len(secret)
        assert secret not in str(events[0])
        assert secret not in " ".join(r.getMessage() for r in caplog.records)


class TestTheOptionalExtraIsPresent:
    """The DEC-F11/F15 enforcement tests must not be skippable (plan item 2.4).

    They sat behind `pytest.importorskip("claude_agent_sdk")`, and `claude-agent-sdk` is an
    optional extra. Any environment synced without `--all-extras` therefore dropped the
    whole sandbox-escape class and reported green; CI was safe only because `make install`
    happens to pass that flag. The skips are gone, so a missing extra now fails at import.
    This test makes the requirement explicit rather than implicit in an ImportError.
    """

    def test_the_sdk_is_importable_in_the_dev_environment(self) -> None:
        assert claude_agent_sdk is not None

    def test_the_llm_extra_is_part_of_the_locked_dev_sync(self) -> None:
        """`make install` uses --all-extras; if that changes, say so here, loudly."""
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        )
        extras = pyproject["project"]["optional-dependencies"]
        assert any("claude-agent-sdk" in dep for dep in extras["llm"])
        makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")
        assert "--all-extras" in makefile


class TestGenerateIsTheRealEntryPoint:
    """`generate()` was covered by nothing at all (plan item 2.3).

    It is the only place the real `claude_agent_sdk.query` is bound. Every other test —
    including the shared LLMClient contract suite's sdk-mocked leg — calls the private
    `_run` with a hand-written transport, the module is coverage-omitted under DEC-F8, and
    the live test skipped for want of an API key. Three independent gates all declined to
    look at the one function that touches the SDK, so a renamed or re-signatured entry
    point would have surfaced only in a user's review.
    """

    async def test_generate_binds_and_calls_the_sdk_query_function(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            seen["prompt"] = prompt
            seen["options"] = options
            return transport([ResultMessage(structured_output={"ok": True})])(
                prompt=prompt, options=options
            )

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        result = await adapter().generate(prompt())

        assert result.payload == {"ok": True}
        # The prompt text and the computed options must actually reach the SDK; binding the
        # symbol without passing them would still have satisfied a laxer assertion.
        assert seen["prompt"] == prompt().user
        assert seen["options"].allowed_tools == prompt().allowed_tools

    async def test_a_missing_sdk_raises_a_typed_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `[llm]` extra is optional, so its absence must be a typed error, not an
        ImportError escaping to the CLI as exit 5."""
        real_import = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "claude_agent_sdk":
                raise ImportError("no claude_agent_sdk")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        with pytest.raises(LLMTransportError):
            await adapter().generate(prompt())


class TestTheSdkResponseChannelIsNotACapability:
    """DEC-F20: deny-by-default denied `StructuredOutput` and broke every real review.

    The defect these tests exist for was invisible to the entire suite because every
    other test in this file hands the model's answer over out of band: `transport()`
    yields `ResultMessage` objects directly, so the hook never runs and the SDK never
    needs its own response channel. The first live end-to-end run failed with "Failed to
    provide valid structured output after 5 attempts" — the harness could not receive an
    answer to any of its six call kinds, on the default configuration.

    So these tests drive the hook with the tool name the SDK actually uses, which is the
    only thing that would have caught it short of a live call.
    """

    @staticmethod
    def _hook(**settings_overrides: Any) -> Any:
        options = adapter(**settings_overrides)._options(prompt())
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        return hook

    @staticmethod
    async def _decide(hook: Any, tool_name: str) -> str:
        result = await hook({"tool_name": tool_name, "tool_input": {}, "cwd": "/"}, "t1", None)
        decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        return str(decision) if decision else "allow"

    async def test_the_default_configuration_permits_the_structured_output_channel(self) -> None:
        """The regression proper: this denied on the default settings and every test passed."""
        assert await self._decide(self._hook(), "StructuredOutput") == "allow"

    async def test_the_protocol_set_is_configuration_not_a_literal(self) -> None:
        """An SDK that renames its response tool is a settings change, not a code change."""
        hook = self._hook(protocol_tools=["SomeRenamedOutputTool"])
        assert await self._decide(hook, "SomeRenamedOutputTool") == "allow"
        assert await self._decide(hook, "StructuredOutput") == "deny"

    async def test_protocol_tools_are_not_unscoped_tools(self) -> None:
        """Emptying `unscoped_tools` is the documented multi-tenant hardening step.

        If the response channel lived in that list, following the security advice would
        break the harness — which is the reason DEC-F20 keeps them separate. Emptying it
        must drop WebSearch and leave the protocol channel alone.
        """
        hook = self._hook(unscoped_tools=[])
        assert await self._decide(hook, "WebSearch") == "deny"
        assert await self._decide(hook, "StructuredOutput") == "allow"

    async def test_other_sdk_internal_tools_stay_denied(self) -> None:
        """Permitting the envelope must not permit everything the SDK might send.

        `ToolSearch` was denied throughout the live run with no ill effect, which is the
        evidence that this branch is narrow enough.
        """
        assert await self._decide(self._hook(), "ToolSearch") == "deny"

    async def test_an_empty_protocol_set_denies_the_channel(self) -> None:
        """States the cost of emptying it, so nobody empties it as a hardening step."""
        assert await self._decide(self._hook(protocol_tools=[]), "StructuredOutput") == "deny"


class TestGlobEscapesThatSurvivedTheFirstFix:
    """DEC-F26/G2: `**/../../../etc/*` was allowed while `../../**/*` was denied.

    `glob_pattern_root` cuts the literal prefix at the FIRST metacharacter, so a pattern
    that opens with one has an empty prefix, and an empty prefix reads as
    "relative to the base directory" — which is allowed. Every escape case in the
    original parametrize put its traversal *before* the first metacharacter, so the check
    passed by describing the shapes it already caught. Moving one character to the left
    walked past the whole control.
    """

    @staticmethod
    def _hook(roots: list[Path]) -> Any:
        options = adapter()._options(prompt().model_copy(update={"allowed_read_roots": roots}))
        (matcher,) = options.hooks["PreToolUse"]
        (hook,) = matcher.hooks
        return hook

    @pytest.mark.parametrize(
        "pattern",
        [
            "**/../../../etc/*",
            "*/../../../etc/passwd",
            "?/../*",
            "[a]/../../../etc/*",
            "**/../../**/*.pem",
            "{a,b}/../../../etc/*",
            # DEC-F26 names three refusals; the original list exercised two. Deleting the
            # backslash clause left the whole suite green — "enumerates the shapes the
            # code already caught", one level down from the defect that entry is about.
            "C:\\Windows\\**",
            "..\\..\\etc\\passwd",
            "*\\..\\secrets",
        ],
    )
    async def test_a_traversal_after_a_metacharacter_is_denied(
        self, tmp_path: Path, pattern: str
    ) -> None:
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": pattern}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_an_ordinary_recursive_pattern_still_works(self, tmp_path: Path) -> None:
        """The refusal must not be so broad that it denies the reviews we want.

        A check that refuses everything is not a check; `**/*.md` under a read root is the
        single most ordinary thing a review does.
        """
        hook = self._hook([tmp_path])
        result = await hook(
            {"tool_name": "Glob", "tool_input": {"pattern": "**/*.md"}, "cwd": str(tmp_path)},
            "t1",
            None,
        )
        assert result == {}

    async def test_a_relative_path_argument_roots_the_pattern_at_the_calls_cwd(
        self, tmp_path: Path
    ) -> None:
        """P7: the glob base was passed through raw, so a RELATIVE `path` resolved against
        this process's working directory rather than the call's `cwd`.

        The path argument and its glob filter were then judged against two different
        roots in the same call — the "resolving against the wrong one is a scoping bypass"
        case the hook's own docstring warns about, applied to the pattern instead.
        """
        (tmp_path / "docs" / "sub").mkdir(parents=True)
        hook = self._hook([tmp_path])
        # `path` is judged against the call's cwd and lands inside the root. The glob
        # filter must be judged against the same base: with the raw value, `"docs"` was
        # resolved against this process's working directory instead, so `sub/*.md` was
        # tested against `<repo>/docs/sub` — a directory in a different tree entirely —
        # and the call was denied for a reason that has nothing to do with the call.
        allowed = await hook(
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": "x", "path": "docs", "glob": "sub/*.md"},
                "cwd": str(tmp_path),
            },
            "t1",
            None,
        )
        assert allowed == {}, "the path argument and its glob filter were judged against "
        "two different roots in the same call"
