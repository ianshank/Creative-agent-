"""The review CLI end-to-end in --offline mode, plus agents/decisions/state commands."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from creative_agent import errors
from creative_agent.cli import app
from creative_agent.errors import ExitCode, ReviewFailedError
from creative_agent.harness import pipeline as pipeline_module
from creative_agent.models.findings import FindingKey
from creative_agent.models.oracle import ArtifactClassRule, ProtocolConfig
from creative_agent.models.state import EscalationEvent
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


class TestOfflineCeilingBanner:
    """An offline run cannot fail on artifact content, and nothing said so (plan 3.3).

    `OfflineLLMClient` returns no claims, so the measurement gates — the entire
    quantitative bar and the only source of a compute-budget Blocker — score nothing;
    every doctrine row comes back not_applicable; and offline classify recommends
    advisory, which caps everything at the oracle's advisory ceiling and exits 0. Anyone
    wiring `--offline` into CI as a gate is running a pass-through, so the run now says so.
    """

    # The banner is written to stderr so it reaches an operator without contaminating the
    # rendered report, which is a byte-stable published contract. CliRunner merges the two
    # by default, so these assert on the merged output and on the report's own structure.
    NOTE = "NOTE: this was an offline run"

    def test_an_offline_auto_run_warns_that_it_cannot_fail(self, env: Path) -> None:
        result = runner.invoke(app, ["review", str(env), "--offline"])
        assert result.exit_code == 0, result.output
        assert self.NOTE in result.output
        assert "could not have failed on content" in result.output
        assert "--mode conformance" in result.output

    def test_the_banner_follows_the_report_rather_than_interleaving_with_it(
        self, env: Path
    ) -> None:
        """The report must remain readable and complete before any commentary."""
        result = runner.invoke(app, ["review", str(env), "--offline"])
        assert result.output.index("## Verification log") < result.output.index(self.NOTE)

    def test_the_banner_is_absent_from_the_machine_readable_report(
        self, env: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "report.json"
        runner.invoke(app, ["review", str(env), "--offline", "--output-json", str(out)])
        assert self.NOTE not in out.read_text(encoding="utf-8")

    def test_conformance_mode_drops_the_cannot_fail_sentence(self, env: Path) -> None:
        """Forced conformance exercises real severities, so that caveat would be wrong."""
        result = runner.invoke(app, ["review", str(env), "--offline", "--mode", "conformance"])
        assert self.NOTE in result.output
        assert "could not have failed on content" not in result.output


class TestTheExitCodesAreObservedAtTheProcessBoundary:
    """G8: every `RUN_ABORTED` assertion in the suite was a class-attribute check.

    `ExitCode.RUN_ABORTED == 6` says what the enum holds, not what the process returns.
    Nothing invoked the CLI and observed a 6, so any regression in `_fail`'s routing — or
    one of these three errors escaping the `except CreativeAgentError` block — would
    silently degrade a retryable abort into exit 5 "unexpected error". The distinction
    between 5 and 6 is the entire content of DEC-F17's addition to a frozen contract: 6
    means retry, 5 means something is broken.
    """

    @pytest.mark.parametrize(
        "error_name",
        ["BudgetExceededError", "LLMTimeoutError", "StateConflictError"],
    )
    def test_a_run_abort_exits_six(
        self, env: Path, monkeypatch: pytest.MonkeyPatch, error_name: str
    ) -> None:
        error_type = getattr(errors, error_name)

        async def abort(self: object, request: object) -> None:
            raise error_type("stopped before a verdict")

        monkeypatch.setattr(pipeline_module.ReviewPipeline, "run", abort)
        result = runner.invoke(app, ["review", str(env), "--offline"])
        assert result.exit_code == int(ExitCode.RUN_ABORTED), result.output

    def test_a_review_failure_still_exits_three(
        self, env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The neighbouring code, asserted alongside so the parametrize above cannot pass
        by mapping everything to 6."""

        async def failed(self: object, request: object) -> None:
            raise ReviewFailedError("verification log incomplete")

        monkeypatch.setattr(pipeline_module.ReviewPipeline, "run", failed)
        result = runner.invoke(app, ["review", str(env), "--offline"])
        assert result.exit_code == int(ExitCode.REVIEW_FAILED), result.output


class TestTheArtifactPathIsCheckedBeforeItIsUsed:
    """DEC-F23: the CLI read the artifact before the containment check existed for it.

    `read_artifact`'s refusal was added to the pipeline's read. The CLI's read, twenty
    lines earlier, had no containment root — and it is the read whose result decides the
    artifact id, which `--reset-state` then acts on. So the file the check was meant to
    refuse was read anyway, and its front matter chose which review-log entry to delete.
    """

    @staticmethod
    def _worktree_with_escaping_symlink(tmp_path: Path) -> tuple[Path, Path]:
        repo = tmp_path / "worktree"
        (repo / "docs").mkdir(parents=True)
        outside = tmp_path / "outside.md"
        outside.write_text(
            "---\nreview-id: someone-elses-artifact\n---\n\n# Not in the worktree\n",
            encoding="utf-8",
        )
        artifact = repo / "docs" / "design.md"
        artifact.symlink_to(outside)
        return repo, artifact

    def test_a_symlink_out_of_the_reviewed_repo_is_refused_by_the_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREATIVE_AGENT_REVIEW_LOG_DIR", str(tmp_path / "review-log"))
        repo, artifact = self._worktree_with_escaping_symlink(tmp_path)
        result = runner.invoke(
            app, ["review", str(artifact), "--offline", "--artifact-repo", str(repo)]
        )
        assert result.exit_code == int(ExitCode.CONFIG_ERROR), result.output

    def test_reset_state_cannot_delete_a_third_artifacts_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The data-loss case, stated as a test.

        The out-of-tree file's front matter names a *different* artifact. Before the fix
        the CLI read it, resolved that id, and `store.reset()` deleted that artifact's
        cycle history and its escalation counter — the counter that drives the cycle-3
        charter-review STOP — before the pipeline refused the same file.
        """
        review_log = tmp_path / "review-log"
        monkeypatch.setenv("CREATIVE_AGENT_REVIEW_LOG_DIR", str(review_log))
        repo, artifact = self._worktree_with_escaping_symlink(tmp_path)
        review_log.mkdir(parents=True)
        victim = review_log / "someone-elses-artifact.md"
        victim.write_text(
            "---\nschema_version: 1\nartifact_id: someone-elses-artifact\ncycle: 3\n"
            "history: []\n---\n\nprior review\n",
            encoding="utf-8",
        )

        runner.invoke(
            app,
            [
                "review",
                str(artifact),
                "--offline",
                "--artifact-repo",
                str(repo),
                "--reset-state",
            ],
        )
        assert victim.exists(), "a refused artifact must not delete another artifact's state"


class TestTheEscalationExitCodeIsMapped:
    """G9: exit 2 was only ever reached through the *severity* half of the condition.

    `if outcome.result.escalation is not None or Severity.BLOCKER in severities` — every
    CLI test that observes exit 2 does so because a Blocker is present, so reducing the
    condition to the Blocker check alone left the suite green. A cycle-3 charter-review
    STOP would then render its block in the report and exit 0, and the hand-off to the
    owner — the entire point of escalation — would never reach CI.
    """

    def test_a_charter_review_stop_exits_two_with_no_blocker_present(
        self, env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_run = pipeline_module.ReviewPipeline.run

        async def escalating(self: Any, request: Any) -> Any:
            outcome = await real_run(self, request)
            escalation = EscalationEvent(
                key=FindingKey(row_id="D1", slug="recurring-defect"),
                cycles=[1, 2, 3],
                message="STOP — charter review triggered",
            )
            return replace(
                outcome,
                result=outcome.result.model_copy(update={"escalation": escalation}),
            )

        # The control: the SAME invocation without the escalation exits 0, so the 2 above
        # cannot be coming from a Blocker in the artifact. Asserting the severities were
        # below Blocker would be the weaker claim — and in an offline run, where every
        # finding is Info-capped, it would be true no matter what the CLI did with the
        # escalation, which is how the original gap survived.
        baseline = runner.invoke(app, ["review", str(env), "--offline", "--reset-state"])
        assert baseline.exit_code == 0, baseline.output

        monkeypatch.setattr(pipeline_module.ReviewPipeline, "run", escalating)
        result = runner.invoke(app, ["review", str(env), "--offline", "--reset-state"])
        assert result.exit_code == int(ExitCode.BLOCKER_OR_STOP), result.output
