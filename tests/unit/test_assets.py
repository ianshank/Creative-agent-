"""Deterministic validation of the shipped Claude Code assets.

`.claude/` is executable configuration. A subagent whose filename no longer matches its
name is unaddressable; a skill with a thin description never triggers; a hook that lost
its executable bit silently stops running. None of that shows up in a normal unit test,
so the assets get their own regression suite — schema checks on every shipped asset,
behavioural checks on the hooks, and named invariants on the contracts other things
depend on (exit codes referenced by the subagent, tools the harness actually uses).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from creative_agent.cli import app
from creative_agent.config import HarnessSettings
from creative_agent.errors import ExitCode
from creative_agent.harness.assets import (
    AGENT_ASSETS,
    EXPECTED_ASSET_KINDS,
    SETTINGS_FILENAME,
    AssetDefect,
    collect,
    default_claude_dir,
    parse_front_matter,
    validate_agent,
    validate_hook,
    validate_settings,
    validate_skill,
)

CLAUDE_DIR = default_claude_dir()
# Tools Claude Code can grant a subagent. Kept explicit so an invented tool name in an
# agent definition fails here rather than at runtime in someone's session.
KNOWN_TOOLS = frozenset(
    {
        "Bash",
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "WebFetch",
        "WebSearch",
        "Task",
        "TodoWrite",
        "NotebookEdit",
        "Agent",
        "SlashCommand",
    }
)

# Every tool Claude Code can use to change a file on disk. The PostToolUse validation hook
# must fire after all of them: a doctrine table edited through `MultiEdit` breaks the
# schema exactly as one edited through `Edit`, and a matcher that omits a tool fails
# silently — the hook simply never runs and nothing reports that it did not.
EDIT_CAPABLE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


@pytest.fixture(scope="module")
def shipped() -> tuple[object, list[AssetDefect]]:
    return collect(CLAUDE_DIR, known_tools=KNOWN_TOOLS)


class TestShippedAssetsAreValid:
    def test_claude_dir_exists(self) -> None:
        assert CLAUDE_DIR.is_dir(), f"no .claude directory at {CLAUDE_DIR}"

    def test_no_defects_in_any_shipped_asset(self, shipped: tuple) -> None:
        _, defects = shipped
        assert not defects, "asset defects:\n" + "\n".join(str(d) for d in defects)

    def test_expected_agents_are_present(self, shipped: tuple) -> None:
        inventory, _ = shipped
        assert "sutton-review" in inventory.agents

    def test_expected_skills_are_present(self, shipped: tuple) -> None:
        inventory, _ = shipped
        assert {"add-oracle", "review-gate", "oracle-rebaseline"} <= set(inventory.skills)

    def test_every_hook_referenced_by_settings_exists(self, shipped: tuple) -> None:
        inventory, _ = shipped
        assert inventory.settings, "settings.json is missing or empty"
        assert set(inventory.settings["hooks"]) == {"SessionStart", "PostToolUse"}


class TestSubagentContract:
    """The subagent delegates to the CLI, so its documented contract must match ours."""

    @pytest.fixture(scope="class")
    @classmethod
    def agent_body(cls) -> str:
        _, body = parse_front_matter(CLAUDE_DIR / "agents" / "sutton-review.md")
        return body

    def test_documents_every_exit_code(self, agent_body: str) -> None:
        for code in ExitCode:
            assert f"{int(code)}" in agent_body, f"exit code {code.name} is undocumented"

    def test_invokes_the_real_cli_command(self, agent_body: str) -> None:
        assert "creative-agent review" in agent_body

    def test_forbids_softening_the_report(self, agent_body: str) -> None:
        """The spec's hard rule; losing this line would quietly change the product."""
        lowered = agent_body.lower()
        assert "unmodified" in lowered
        assert "do not soften" in lowered

    def test_declares_only_tools_it_uses(self) -> None:
        meta, _ = parse_front_matter(CLAUDE_DIR / "agents" / "sutton-review.md")
        declared = {t.strip() for t in str(meta["tools"]).split(",")}
        assert "Bash" in declared, "the agent must be able to run the CLI"
        assert declared <= KNOWN_TOOLS


class TestSkillContracts:
    def test_review_gate_names_the_real_targets(self) -> None:
        _, body = parse_front_matter(CLAUDE_DIR / "skills" / "review-gate" / "SKILL.md")
        for command in ("ruff check", "mypy", "lint-imports", "pytest", "oracles validate"):
            assert command in body, f"review-gate omits {command}"

    def test_add_oracle_points_at_the_shipped_oracle(self) -> None:
        _, body = parse_front_matter(CLAUDE_DIR / "skills" / "add-oracle" / "SKILL.md")
        assert "src/creative_agent/data/oracles/sutton.v2.yaml" in body
        oracle_path = "src/creative_agent/data/oracles/sutton.v2.yaml"
        assert (Path(__file__).resolve().parents[2] / oracle_path).is_file()

    def test_rebaseline_skill_uses_the_real_subcommand(self) -> None:
        _, body = parse_front_matter(CLAUDE_DIR / "skills" / "oracle-rebaseline" / "SKILL.md")
        assert "oracles rebaseline" in body

    def test_inspect_state_commands_actually_resolve(self) -> None:
        """A skill's prose is a claim, not a test — verify every command it names still
        exists by actually invoking it through the real Typer app (in-process, via
        CliRunner), the same spirit as TestHookBehaviour running the real hooks instead of
        only checking they're present. A renamed or removed subcommand would otherwise rot
        silently."""
        _, body = parse_front_matter(CLAUDE_DIR / "skills" / "inspect-state" / "SKILL.md")
        pattern = r"creative-agent ([a-z]+(?:-[a-z]+)*(?: [a-z]+(?:-[a-z]+)*)?)"
        commands = sorted(set(re.findall(pattern, body)))
        assert commands, "no `creative-agent <subcommand>` strings found — the scan is inert"
        expected = {
            "oracles list",
            "agents list",
            "decisions check",
            "state show",
            "assets validate",
        }
        assert expected <= set(commands)

        runner = CliRunner()
        for command in commands:
            result = runner.invoke(app, [*command.split(), "--help"])
            label = f"creative-agent {command} --help"
            assert result.exit_code == 0, f"`{label}` failed: {result.output}"


class TestSettingsMatchTheHarness:
    @pytest.fixture(scope="class")
    @classmethod
    def settings_json(cls) -> dict:
        return json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))

    def test_allowlist_covers_the_gate_commands(self, settings_json: dict) -> None:
        allowed = " ".join(settings_json["permissions"]["allow"])
        for command in ("creative-agent", "pytest", "ruff", "mypy"):
            assert command in allowed, f"{command} is not pre-approved; every run prompts"

    def test_no_blanket_bash_permission(self, settings_json: dict) -> None:
        """A bare Bash(*) grant would defeat the point of an allowlist."""
        assert "Bash(*)" not in settings_json["permissions"]["allow"]

    @pytest.mark.parametrize("tool", sorted(EDIT_CAPABLE_TOOLS))
    def test_post_tool_use_matcher_names_every_edit_capable_tool(
        self, settings_json: dict, tool: str
    ) -> None:
        """CLAUDE.md says the hook re-validates after "any edit"; the matcher must agree.

        It listed only `Edit|Write`, so a `MultiEdit` or `NotebookEdit` to a doctrine table
        never fired the validator and the schema break surfaced in CI instead of at edit
        time. Nothing tested the wiring at all — `TestHookBehaviour` runs the scripts by
        subprocess, which bypasses the matcher entirely, so the hook looked well-tested
        while the tool list that decides whether it ever runs was unasserted.
        """
        matchers = [entry.get("matcher", "") for entry in settings_json["hooks"]["PostToolUse"]]
        named = {name for matcher in matchers for name in matcher.split("|")}
        assert tool in named, f"a {tool} edit would not re-validate the data it changed"


@pytest.fixture
def broken_oracle_dir(tmp_path: Path) -> Iterator[Path]:
    """A schema-invalid oracle in a throwaway search path the hook will actually load.

    The path ends in `data/oracles/` because the hook decides whether to run oracle
    validation by matching that substring in its payload.
    """
    directory = tmp_path / "data" / "oracles"
    directory.mkdir(parents=True)
    (directory / "_hooktest_broken.yaml").write_text(
        "schema_version: 1\noracle_id: broken\n", encoding="utf-8"
    )
    yield directory
    shutil.rmtree(tmp_path, ignore_errors=True)


class TestHookBehaviour:
    """Hooks are shell scripts with real side effects; assert what they actually do."""

    @staticmethod
    def _run(
        script: str, payload: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CLAUDE_DIR / "hooks" / script)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={**os.environ, **(env or {})},
        )

    def test_oracle_hook_passes_for_valid_data(self) -> None:
        payload = json.dumps(
            {"tool_input": {"file_path": "src/creative_agent/data/oracles/sutton.v2.yaml"}}
        )
        assert self._run("validate-data.sh", payload).returncode == 0

    def test_oracle_hook_ignores_unrelated_edits(self) -> None:
        payload = json.dumps({"tool_input": {"file_path": "README.md"}})
        result = self._run("validate-data.sh", payload)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_oracle_hook_blocks_on_invalid_data(self, broken_oracle_dir: Path) -> None:
        """Exit 2 is what feeds the error back to Claude to fix immediately.

        The broken oracle is written into a tmp search path, never into the packaged
        `src/creative_agent/data/oracles/`: an earlier version of this test wrote there
        and relied on a `finally` to clean up, so an interrupted or killed run left
        `_hooktest_broken.yaml` in the checkout and broke `make oracles` for everything
        afterwards. `TestTheSuiteDoesNotMutateTheRepository` states the rule this now
        keeps: the suite must not write into the repository.
        """
        broken = broken_oracle_dir / "_hooktest_broken.yaml"
        payload = json.dumps({"tool_input": {"file_path": str(broken)}})
        result = self._run(
            "validate-data.sh",
            payload,
            env={"CREATIVE_AGENT_ORACLE_SEARCH_PATHS": str(broken_oracle_dir)},
        )
        assert result.returncode == 2
        assert "Oracle validation failed" in result.stderr

    def test_the_broken_oracle_never_lands_in_the_checkout(self) -> None:
        """Directly asserts what the fixture above is for: whatever happens to a run, no
        test artefact is left in the packaged oracle directory."""
        packaged = Path(__file__).resolve().parents[2] / "src/creative_agent/data/oracles"
        stray = sorted(p.name for p in packaged.glob("_hooktest*"))
        assert not stray, f"test data leaked into the packaged oracle directory: {stray}"

    def test_session_start_hook_reports_readiness(self) -> None:
        result = self._run("session-start.sh", "")
        assert result.returncode == 0
        assert "creative-agent ready" in result.stdout


class TestAssetValidatorItself:
    """The validator is the thing catching the defects, so test it against known-bad
    inputs — otherwise a broken validator silently passes everything."""

    def test_missing_front_matter_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("no front matter here", encoding="utf-8")
        assert validate_agent(path)[0].message.startswith("missing YAML front matter")

    def test_unparseable_front_matter_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\na: [unclosed\n---\nbody\n", encoding="utf-8")
        assert "unparseable" in validate_agent(path)[0].message

    def test_name_filename_mismatch_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.md"
        path.write_text(
            "---\nname: right\ndescription: " + "d" * 60 + "\n---\nbody\n", encoding="utf-8"
        )
        assert any("filename must match" in d.message for d in validate_agent(path))

    def test_unknown_frontmatter_key_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text(
            "---\nname: a\ndescription: " + "d" * 60 + "\ntoolz: Bash\n---\nbody\n",
            encoding="utf-8",
        )
        assert any("unknown frontmatter keys" in d.message for d in validate_agent(path))

    def test_unknown_tool_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text(
            "---\nname: a\ndescription: " + "d" * 60 + "\ntools: Telepathy\n---\nbody\n",
            encoding="utf-8",
        )
        defects = validate_agent(path, known_tools=KNOWN_TOOLS)
        assert any("unknown tools" in d.message for d in defects)

    def test_thin_description_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("---\nname: a\ndescription: short\n---\nbody\n", encoding="utf-8")
        assert any("unlikely to trigger" in d.message for d in validate_agent(path))

    def test_empty_body_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("---\nname: a\ndescription: " + "d" * 60 + "\n---\n", encoding="utf-8")
        assert any("empty body" in d.message for d in validate_agent(path))

    def test_skill_directory_mismatch_is_reported(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "wrong-dir"
        skill_dir.mkdir()
        path = skill_dir / "SKILL.md"
        path.write_text(
            "---\nname: right-name\ndescription: " + "d" * 60 + "\n---\nbody\n", encoding="utf-8"
        )
        assert any("directory must match" in d.message for d in validate_skill(path))

    def test_non_executable_hook_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "h.sh"
        path.write_text("#!/usr/bin/env bash\nset -e\n", encoding="utf-8")
        path.chmod(0o644)
        assert any("not executable" in d.message for d in validate_hook(path))

    def test_hook_without_shebang_or_set_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "h.sh"
        path.write_text("echo hi\n", encoding="utf-8")
        path.chmod(0o755)
        messages = [d.message for d in validate_hook(path)]
        assert any("missing shebang" in m for m in messages)
        assert any("set -e" in m for m in messages)

    def test_settings_referencing_a_missing_hook_is_reported(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/absent.sh",
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        defects = validate_settings(settings, tmp_path)
        assert any("missing script absent.sh" in d.message for d in defects)

    def test_invalid_settings_json_is_reported(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text("{not json", encoding="utf-8")
        assert "invalid JSON" in validate_settings(settings, tmp_path)[0].message

    def test_collect_on_an_empty_directory_reports_every_missing_kind(self, tmp_path: Path) -> None:
        """An empty inventory is a failure, not a clean bill of health.

        This test previously asserted the opposite — that `collect` on an empty directory
        is clean — which enshrined the defect: every walk in `collect` is guarded by
        `is_dir()`, so renaming `.claude/agents` to `.claude/agent` (or deleting the whole
        tree) validated nothing and reported "ok: all assets valid". The oracle path
        already refuses that shape ("no oracle files found"); the asset path now does too.
        """
        inventory, defects = collect(tmp_path)
        assert not inventory.agents and not inventory.skills and not inventory.hooks
        reported = " ".join(str(d) for d in defects)
        for kind in EXPECTED_ASSET_KINDS:
            assert kind.directory in reported, f"a missing {kind.name} directory went unreported"
        assert SETTINGS_FILENAME in reported

    def test_collect_reports_a_kind_whose_directory_was_renamed(self, tmp_path: Path) -> None:
        """The concrete way this happens: everything else is present and valid, so the
        run looks healthy while one asset kind has quietly stopped being validated."""
        shutil.copytree(CLAUDE_DIR, tmp_path / "claude")
        claude_dir = tmp_path / "claude"
        (claude_dir / AGENT_ASSETS.directory).rename(claude_dir / "agent")

        _, defects = collect(claude_dir, known_tools=KNOWN_TOOLS)
        assert any(AGENT_ASSETS.directory in str(d) for d in defects), (
            "a renamed agents/ directory produced no defect; `assets validate` would pass"
        )

    def test_a_complete_directory_reports_no_missing_kinds(self, tmp_path: Path) -> None:
        """The failure direction: a guard that always fires would be turned off."""
        shutil.copytree(CLAUDE_DIR, tmp_path / "claude")
        _, defects = collect(tmp_path / "claude", known_tools=KNOWN_TOOLS)
        assert not defects, "the shipped .claude tree must not trip the emptiness guard"


class TestAgentToolsMatchHarnessCapabilities:
    def test_default_agent_tools_are_known(self) -> None:
        """settings-driven SDK tool scoping must name tools that actually exist."""
        assert set(HarnessSettings().agent_tools) <= KNOWN_TOOLS

    def test_fetch_tools_are_a_subset_of_agent_tools(self) -> None:
        settings = HarnessSettings()
        assert set(settings.fetch_tool_names) <= set(settings.agent_tools), (
            "a fetch tool the session cannot use would reject every verification entry"
        )
