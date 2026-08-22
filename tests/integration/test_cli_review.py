"""The review CLI end-to-end in --offline mode, plus agents/decisions/state commands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from creative_agent.cli import app
from creative_agent.errors import ExitCode, ReviewFailedError
from creative_agent.harness import pipeline as pipeline_module
from creative_agent.models.oracle import ArtifactClassRule, ProtocolConfig
from tests.factories import make_oracle

runner = CliRunner()

ARTIFACT = """# Streaming Controller Design

We claim conformance to the Alberta Plan.

gamma = 0.99

gamma = 0.95
"""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CREATIVE_AGENT_REVIEW_LOG_DIR", str(tmp_path / "review-log"))
    artifact = tmp_path / "controller-design.md"
    artifact.write_text(ARTIFACT, encoding="utf-8")
    return artifact


class TestOfflineReview:
    def test_offline_review_runs_deterministic_checks(self, env: Path) -> None:
        result = runner.invoke(app, ["review", str(env), "--offline"])
        # The duplicate gamma definition is caught deterministically, but offline runs
        # are fail-closed advisory, and Info is the advisory ceiling (spec severity
        # table) — so the Blocker is reported capped and the exit is clean.
        assert result.exit_code == 0, result.output
        assert "VERDICT" in result.output
        assert "mode=advisory (mode uncertain)" in result.output  # markers present
        assert "gamma" in result.output
        assert "Info (was Blocker)" in result.output

    def test_offline_clean_artifact_exits_zero(self, env: Path, tmp_path: Path) -> None:
        clean = tmp_path / "clean-doc.md"
        clean.write_text("# Clean\n\nNothing quantitative here.\n", encoding="utf-8")
        result = runner.invoke(app, ["review", str(clean), "--offline"])
        assert result.exit_code == 0, result.output

    def test_output_json_written(self, env: Path, tmp_path: Path) -> None:
        out = tmp_path / "report.json"
        runner.invoke(app, ["review", str(env), "--offline", "--output-json", str(out)])
        assert out.exists()
        assert '"contract_version"' in out.read_text(encoding="utf-8")

    def test_cycle_advances_and_state_show(self, env: Path) -> None:
        runner.invoke(app, ["review", str(env), "--offline"])
        runner.invoke(app, ["review", str(env), "--offline"])
        result = runner.invoke(app, ["state", "show", "controller-design"])
        assert result.exit_code == 0
        assert "cycles: 2" in result.output

    def test_reset_state(self, env: Path) -> None:
        runner.invoke(app, ["review", str(env), "--offline"])
        runner.invoke(app, ["review", str(env), "--offline", "--reset-state"])
        result = runner.invoke(app, ["state", "show", "controller-design"])
        assert "cycles: 1" in result.output

    def test_unknown_agent_is_config_error(self, env: Path) -> None:
        result = runner.invoke(app, ["review", str(env), "--agent", "nope", "--offline"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_invalid_mode_rejected(self, env: Path) -> None:
        result = runner.invoke(app, ["review", str(env), "--mode", "strict", "--offline"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)


class TestAgentsAndDecisions:
    def test_agents_list(self) -> None:
        result = runner.invoke(app, ["agents", "list"])
        assert result.exit_code == 0
        assert "sutton-review\toracle=sutton" in result.output

    def test_decisions_check_flags_missing(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["decisions", "check", "--repo", str(tmp_path)])
        assert result.exit_code == int(ExitCode.FINDINGS_MAJOR)
        assert "DEC-S1" in result.output

    def test_decisions_check_passes_when_confirmed(self, tmp_path: Path) -> None:
        log = tmp_path / "docs" / "decision-log.md"
        log.parent.mkdir(parents=True)
        entries = "\n\n".join(f"## DEC-S{i} — decision — CONFIRMED" for i in range(1, 7))
        log.write_text(f"# Log\n\n{entries}\n", encoding="utf-8")
        result = runner.invoke(app, ["decisions", "check", "--repo", str(tmp_path)])
        assert result.exit_code == 0, result.output


class TestExitCodeContract:
    """The exit-code table is a frozen contract CI consumers depend on."""

    def test_blocker_exits_two_under_conformance_mode(self, env: Path) -> None:
        # --mode conformance skips fail-closed auto-detection, so the duplicate-gamma
        # internal contradiction keeps Blocker severity instead of the advisory cap.
        result = runner.invoke(app, ["review", str(env), "--offline", "--mode", "conformance"])
        assert result.exit_code == int(ExitCode.BLOCKER_OR_STOP), result.output
        assert "mode=conformance" in result.output

    def test_review_failure_exits_three(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(self: object, request: object) -> None:
            raise ReviewFailedError("incomplete verification log")

        monkeypatch.setattr(pipeline_module.ReviewPipeline, "run", boom)
        result = runner.invoke(app, ["review", str(env), "--offline"])
        assert result.exit_code == int(ExitCode.REVIEW_FAILED)

    def test_missing_artifact_is_usage_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["review", str(tmp_path / "absent.md"), "--offline"])
        assert result.exit_code != 0

    def test_debug_flag_emits_json_logs(self, env: Path) -> None:
        result = runner.invoke(
            app, ["--debug", "--log-format", "json", "review", str(env), "--offline"]
        )
        assert result.exit_code == 0, result.output

    def test_invalid_log_format_is_config_error(self, env: Path) -> None:
        result = runner.invoke(app, ["--log-format", "yaml", "oracles", "list"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_state_show_lists_findings(self, env: Path) -> None:
        runner.invoke(app, ["review", str(env), "--offline", "--mode", "conformance"])
        result = runner.invoke(app, ["state", "show", "controller-design"])
        assert result.exit_code == 0
        assert "cycle 1" in result.output
        assert "disposition=open" in result.output

    def test_state_show_for_unknown_artifact_is_empty(self) -> None:
        result = runner.invoke(app, ["state", "show", "never-reviewed"])
        assert result.exit_code == 0
        assert "cycles: 0" in result.output


class TestSecondOracleNeedsNoCode:
    """The framework's central claim: a new corpus is a new YAML file."""

    def test_review_runs_against_a_non_sutton_oracle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = make_oracle(
            oracle_id="other",
            name="unrelated corpus",
            artifact_classes=[
                ArtifactClassRule(name="policy_memo"),
                ArtifactClassRule(name="lit_review", source_quality_only=True),
            ],
            protocol=ProtocolConfig(
                escalation_cycle=3,
                unverified_marker="[unverified]",
                offline_artifact_class="policy_memo",
            ),
        )
        oracle_dir = tmp_path / "oracles"
        oracle_dir.mkdir()
        (oracle_dir / "other.yaml").write_text(
            yaml.safe_dump(other.model_dump(mode="json", exclude_none=True), sort_keys=False),
            encoding="utf-8",
        )
        monkeypatch.setenv("CREATIVE_AGENT_ORACLE_SEARCH_PATHS", str(oracle_dir))
        artifact = tmp_path / "memo.md"
        artifact.write_text("# A Memo\n\nNothing doctrinal here.\n", encoding="utf-8")
        result = runner.invoke(app, ["review", str(artifact), "--oracle", "other", "--offline"])
        assert result.exit_code == 0, result.output
        assert "Oracle: other" in result.output


class TestAssetsCommand:
    """The assets validator must be usable outside the test suite (CI, hooks, humans)."""

    def test_shipped_assets_validate(self) -> None:
        result = runner.invoke(app, ["assets", "validate"])
        assert result.exit_code == 0, result.output
        assert "ok: all assets valid" in result.output

    def test_defective_assets_exit_nonzero(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "mismatched.md").write_text(
            "---\nname: other-name\ndescription: " + "d" * 60 + "\n---\nbody\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["assets", "validate", "--claude-dir", str(tmp_path)])
        assert result.exit_code == int(ExitCode.FINDINGS_MAJOR)
        assert "filename must match" in result.output

    def test_missing_directory_is_config_error(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["assets", "validate", "--claude-dir", str(tmp_path / "absent")]
        )
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)
