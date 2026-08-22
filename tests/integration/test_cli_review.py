"""The review CLI end-to-end in --offline mode, plus agents/decisions/state commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from creative_agent.cli import app
from creative_agent.errors import ExitCode

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
