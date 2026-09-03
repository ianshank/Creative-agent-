"""Asset body references resolve, or they are defects (DEC-F29).

`assets.py` validates an asset's shape. This validates its *instructions*, which is the
part an agent actually follows: a skill naming a renamed script, an undeclared `make`
target, an unregistered subcommand, or a decision nobody wrote. Each of those passes every
other gate in this repository and fails silently mid-session, in someone else's context.

Each check is tested in both directions, because a validator is two claims — it flags what
is broken, and it stays quiet on what is not. The second is the one that decides whether
anyone keeps running it: the first version of `check_paths` flagged three paths in
`live-verify`, and they were the paths that skill tells the reader to *delete* after a live
run. A checker whose false positives are the instructions it validates trains people to
ignore it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from creative_agent.cli import registered_subcommands
from creative_agent.harness.asset_references import (
    DEFAULT_REFERENCE_RULES,
    ReferenceContext,
    ReferenceRules,
    check_decisions,
    check_make_targets,
    check_paths,
    check_subcommands,
    missing_references,
)
from creative_agent.harness.assets import collect, default_claude_dir

SUBCOMMANDS = frozenset({"review", "oracles", "assets"})


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A miniature repository with one of everything the checks resolve against."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_guard.py").write_text("# tool\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decision-log.md").write_text(
        "## DEC-F1 — first — CONFIRMED\n\n## DEC-F2 — second — CONFIRMED\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        ".PHONY: gate\ngate: lint test\n\nlint:\n\techo lint\n\ntest:\n\techo test\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def context(repo: Path) -> ReferenceContext:
    return ReferenceContext(repo_root=repo, known_subcommands=SUBCOMMANDS)


class TestPathReferences:
    def test_a_path_that_does_not_exist_is_a_defect(self, context: ReferenceContext) -> None:
        (defect,) = check_paths("Run `scripts/renamed_guard.py` to check.", context)
        assert "scripts/renamed_guard.py" in defect

    def test_a_path_that_exists_is_silent(self, context: ReferenceContext) -> None:
        assert check_paths("Run `scripts/verify_guard.py` to check.", context) == []

    def test_a_path_inside_a_fenced_block_is_checked(self, context: ReferenceContext) -> None:
        """Commands live in fenced blocks; a reference there is the most load-bearing kind."""
        body = "```bash\nuv run python scripts/gone.py --flag\n```\n"
        assert len(check_paths(body, context)) == 1

    def test_prose_outside_code_is_not_scanned(self, context: ReferenceContext) -> None:
        """An asset may legitimately discuss docs/design.md as an example artifact.

        Scanning prose would flag every hypothetical the asset uses to explain itself, and
        the fix for that noise would be to stop reading the output.
        """
        assert check_paths("Review the file at docs/design.md in the target repo.", context) == []

    @pytest.mark.parametrize(
        "token",
        ["`src/**/*.py`", "`docs/<artifact>.md`", "`scripts/$NAME.py`", "`tests/{a,b}.py`"],
    )
    def test_globs_and_placeholders_are_not_treated_as_paths(
        self, context: ReferenceContext, token: str
    ) -> None:
        assert check_paths(f"See {token} for the pattern.", context) == []

    def test_a_path_under_a_runtime_prefix_is_not_a_stale_reference(
        self, context: ReferenceContext
    ) -> None:
        """`docs/review-log/<id>.md` is produced by running the harness.

        This is the false-positive class the first version had: `live-verify` names three
        such paths in the `rm` command that cleans up after a live run, so the checker
        flagged the very instructions it was validating.
        """
        body = "```bash\nrm -rf docs/review-log/live-probe.md docs/review-log/live-probe\n```\n"
        assert check_paths(body, context) == []

    def test_the_runtime_prefixes_are_configuration(self, repo: Path) -> None:
        """A repository that writes its output elsewhere says so, rather than editing code."""
        rules = ReferenceRules(
            path_prefixes=("out/",), makefile="Makefile", decision_log="docs/decision-log.md"
        )
        strict = ReferenceContext(repo_root=repo, rules=rules)
        assert len(check_paths("`out/report.md`", strict)) == 1
        lenient = ReferenceContext(
            repo_root=repo,
            rules=ReferenceRules(
                path_prefixes=("out/",),
                makefile="Makefile",
                decision_log="docs/decision-log.md",
                runtime_prefixes=("out/",),
            ),
        )
        assert check_paths("`out/report.md`", lenient) == []


class TestMakeTargetReferences:
    def test_an_undeclared_target_is_a_defect(self, context: ReferenceContext) -> None:
        (defect,) = check_make_targets("Run `make nonexistent` first.", context)
        assert "nonexistent" in defect
        assert "gate" in defect, "the message must name the targets that do exist"

    def test_a_declared_target_is_silent(self, context: ReferenceContext) -> None:
        assert check_make_targets("Run `make gate` before pushing.", context) == []

    def test_a_prerequisite_only_target_still_counts(self, context: ReferenceContext) -> None:
        """`lint` is declared as a rule, not just named as a prerequisite of `gate`."""
        assert check_make_targets("Run `make lint`.", context) == []

    def test_a_missing_makefile_yields_no_defects(self, tmp_path: Path) -> None:
        """The absence of the file being resolved against is a fact about the repository.

        Reporting every `make` reference as broken would bury real findings under a wall of
        consequences from one missing file.
        """
        empty = ReferenceContext(repo_root=tmp_path, known_subcommands=SUBCOMMANDS)
        assert check_make_targets("Run `make gate`.", empty) == []


class TestSubcommandReferences:
    def test_an_unregistered_subcommand_is_a_defect(self, context: ReferenceContext) -> None:
        (defect,) = check_subcommands("Run `creative-agent verify-everything`.", context)
        assert "verify-everything" in defect

    def test_a_registered_subcommand_is_silent(self, context: ReferenceContext) -> None:
        assert check_subcommands("Run `creative-agent review doc.md`.", context) == []

    def test_no_known_subcommands_means_no_defects(self, repo: Path) -> None:
        """A caller that cannot enumerate the CLI must not have every invocation flagged."""
        blind = ReferenceContext(repo_root=repo)
        assert check_subcommands("Run `creative-agent anything`.", blind) == []


class TestDecisionReferences:
    def test_an_unlogged_decision_is_a_defect(self, context: ReferenceContext) -> None:
        (defect,) = check_decisions("This is required by DEC-F999.", context)
        assert "DEC-F999" in defect

    def test_a_logged_decision_is_silent(self, context: ReferenceContext) -> None:
        assert check_decisions("This is required by DEC-F2.", context) == []

    def test_a_missing_decision_log_yields_no_defects(self, tmp_path: Path) -> None:
        empty = ReferenceContext(repo_root=tmp_path)
        assert check_decisions("See DEC-F1.", empty) == []


class TestComposition:
    def test_every_check_runs_and_findings_are_deduplicated(
        self, context: ReferenceContext
    ) -> None:
        body = (
            "Run `scripts/gone.py`, then `make nope`, then `creative-agent nope2`, "
            "per DEC-F999. Then run `scripts/gone.py` again."
        )
        defects = missing_references(body, context)
        assert len(defects) == 4, "one per broken reference, and the repeated path only once"

    def test_an_asset_with_nothing_broken_produces_nothing(self, context: ReferenceContext) -> None:
        body = "Run `make gate`, then `scripts/verify_guard.py`, per DEC-F1."
        assert missing_references(body, context) == []

    def test_the_check_set_is_injectable(self, context: ReferenceContext) -> None:
        """The checks are data, so a caller can run a subset without a flag per check."""
        body = "`scripts/gone.py` and `make nope`"
        assert len(missing_references(body, context, checks=(check_paths,))) == 1


class TestTheShippedAssetsResolve:
    """The end-to-end assertion: this repository's own assets must pass.

    This is what makes the check a gate rather than a library. It is also the test that
    fails the day someone renames `scripts/verify_guard.py` without updating the skill that
    tells an agent to run it.
    """

    def test_every_shipped_asset_reference_resolves(self) -> None:
        claude_dir = default_claude_dir()
        _, defects = collect(
            claude_dir,
            references=ReferenceContext(
                repo_root=claude_dir.parent,
                known_subcommands=frozenset(registered_subcommands()),
            ),
        )
        assert [str(d) for d in defects] == []

    def test_the_default_rules_cover_the_directories_assets_actually_reference(self) -> None:
        """A prefix list that misses a directory silently stops checking it — the same
        failure as a coverage floor whose path no longer matches anything."""
        for directory in ("src/", "tests/", "scripts/", "docs/", ".claude/"):
            assert directory in DEFAULT_REFERENCE_RULES.path_prefixes


class TestOrdinaryEnglishIsNotAReference:
    """G7: two of the four checks scanned the raw body, so prose fired them.

    `check_paths` restricted itself to code spans and fenced blocks from the start, with
    the reason in the module docstring. The `make` and subcommand checks did not, and the
    shipped assets passed only because every such phrase happened to sit in front matter,
    which is stripped before the body is scanned. The first skill that says "make sure"
    would have failed `make assets`.
    """

    @pytest.mark.parametrize(
        "sentence",
        [
            "Please make sure the gate is green before pushing.",
            "First make certain the artifact exists.",
            "You may make changes to the oracle data.",
        ],
    )
    def test_the_word_make_in_prose_is_not_a_target_reference(
        self, context: ReferenceContext, sentence: str
    ) -> None:
        assert check_make_targets(sentence, context) == []

    @pytest.mark.parametrize(
        "sentence",
        [
            "The creative-agent harness reviews design documents.",
            "This is how creative-agent reviews work.",
        ],
    )
    def test_the_product_name_in_prose_is_not_a_subcommand(
        self, context: ReferenceContext, sentence: str
    ) -> None:
        assert check_subcommands(sentence, context) == []

    def test_the_same_words_inside_code_are_still_checked(self, context: ReferenceContext) -> None:
        """The narrowing must not disarm the check where it matters."""
        assert len(check_make_targets("Run `make sure` now.", context)) == 1
        assert len(check_subcommands("```bash\ncreative-agent harness\n```\n", context)) == 1
