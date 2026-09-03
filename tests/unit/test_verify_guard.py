"""Tests for the guard checker.

The tool's own guard has to hold, and the way to show that is to run it against a toy
package where the answer is known both ways: a strong test that asserts an outcome, and a
weak one that asserts its own fixture. If the checker cannot tell those apart it is
worthless, and it would be worthless in the confident direction — reporting that a guard
holds is exactly the reassurance nobody should take on trust from this repository.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import verify_guard
from scripts.verify_guard import (
    EXIT_CANNOT_CHECK,
    EXIT_GUARD_HOLDS,
    EXIT_GUARD_WEAK,
    Edit,
    Guard,
    apply,
    load_spec,
    main,
)

SOURCE = textwrap.dedent(
    """
    def permitted(name: str) -> bool:
        if name == "StructuredOutput":
            return True
        return False
    """
).strip()

STRONG_TEST = textwrap.dedent(
    """
    from toy import permitted

    def test_the_protocol_channel_is_permitted():
        assert permitted("StructuredOutput") is True

    def test_everything_else_is_denied():
        assert permitted("Bash") is False
    """
).strip()

WEAK_TEST = textwrap.dedent(
    """
    from toy import permitted

    def test_that_asserts_its_own_input():
        name = "Bash"
        assert permitted(name) is False
    """
).strip()


@pytest.fixture()
def toy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "toy.py").write_text(SOURCE + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    return tmp_path


def _revert_args(toy_dir: Path, test_file: str) -> list[str]:
    """The revert removes the branch that permits the protocol channel."""
    return [
        "--file",
        "toy.py",
        "--find",
        '    if name == "StructuredOutput":\n        return True\n',
        "--replace",
        "",
        "--test",
        test_file,
        "--name",
        "toy protocol channel",
    ]


class TestTheCheckerDistinguishesStrongFromWeak:
    def test_a_test_that_asserts_the_outcome_is_reported_as_holding(self, toy: Path) -> None:
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        assert main(_revert_args(toy, "test_strong.py")) == EXIT_GUARD_HOLDS

    def test_a_test_that_asserts_its_own_fixture_is_reported_as_weak(self, toy: Path) -> None:
        """The weak test never calls the reverted branch, so it passes either way.

        This is the shape that shipped here five times: the assertion is true of the
        implementation and of its absence, so it distinguishes nothing.
        """
        (toy / "test_weak.py").write_text(WEAK_TEST + "\n", encoding="utf-8")
        assert main(_revert_args(toy, "test_weak.py")) == EXIT_GUARD_WEAK

    def test_the_source_is_restored_either_way(self, toy: Path) -> None:
        (toy / "test_weak.py").write_text(WEAK_TEST + "\n", encoding="utf-8")
        main(_revert_args(toy, "test_weak.py"))
        assert (toy / "toy.py").read_text(encoding="utf-8") == SOURCE + "\n"


class TestTheCheckerRefusesToGuessWhenItCannotCheck:
    def test_a_red_baseline_is_not_reported_as_a_weak_guard(self, toy: Path) -> None:
        """A failing baseline says nothing about the guard, and saying otherwise would
        send someone rewriting a test whose only problem is that the tree is broken."""
        (toy / "test_broken.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")
        assert main(_revert_args(toy, "test_broken.py")) == EXIT_CANNOT_CHECK

    def test_a_find_string_that_does_not_match_is_a_tool_error(self, toy: Path) -> None:
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        args = _revert_args(toy, "test_strong.py")
        args[args.index("--find") + 1] = "text that is not in the file"
        assert main(args) == EXIT_CANNOT_CHECK

    def test_an_ambiguous_find_string_is_refused(self, toy: Path) -> None:
        """Two matches means the revert is not the one the operator described."""
        (toy / "toy.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
        with pytest.raises(LookupError, match="appears 2 times"):
            apply(
                Guard(
                    name="ambiguous",
                    tests=("test_strong.py",),
                    edits=(Edit(file=Path("toy.py"), find="x = 1\n", replace=""),),
                )
            )
        assert (toy / "toy.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"

    def test_a_failed_multi_file_apply_rolls_back_what_it_already_changed(self, toy: Path) -> None:
        """A half-applied revert would poison the next run's baseline silently."""
        (toy / "other.py").write_text("keep me\n", encoding="utf-8")
        with pytest.raises(LookupError):
            apply(
                Guard(
                    name="partial",
                    tests=("test_strong.py",),
                    edits=(
                        Edit(file=Path("other.py"), find="keep me", replace="changed"),
                        Edit(file=Path("toy.py"), find="not present anywhere", replace=""),
                    ),
                )
            )
        assert (toy / "other.py").read_text(encoding="utf-8") == "keep me\n"


class TestSpecFiles:
    def test_a_spec_drives_the_same_check(self, toy: Path) -> None:
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        spec = toy / "guard.json"
        spec.write_text(
            json.dumps(
                {
                    "name": "toy protocol channel",
                    "tests": ["test_strong.py"],
                    "edits": [
                        {
                            "file": "toy.py",
                            "find": '    if name == "StructuredOutput":\n        return True\n',
                            "replace": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert main(["--spec", str(spec)]) == EXIT_GUARD_HOLDS

    def test_a_spec_missing_its_halves_is_refused(self, toy: Path) -> None:
        spec = toy / "bad.json"
        spec.write_text(json.dumps({"name": "x", "tests": ["a"]}), encoding="utf-8")
        with pytest.raises(SystemExit, match="needs both"):
            load_spec(spec)


class TestARevertThatDoesNotRunIsNotAKill:
    """The tool's own worst failure: reporting GUARD HOLDS for a broken revert.

    `run_tests` used to return `returncode == 0`, so *any* nonzero pytest exit read as
    "the named tests failed" and therefore as "the guard holds". pytest exits nonzero for
    collection errors (2), internal errors (3), usage errors (4) and no-tests-collected
    (5) — none of which is a test expressing an opinion.

    Reproduced against this very tool: a revert deleting an `if` header leaves an
    `IndentationError`, and the canonical *weak* test — the one asserting its own input,
    which this file elsewhere requires be reported weak — was certified as holding.

    This is the direction that matters. A tool that under-reports gets ignored; a tool
    that hands out false reassurance gets believed, and it is the instrument that
    `CLAUDE.md`, the `guard-check` skill and the `gap-auditor` agent all now rest on.
    """

    def test_a_revert_that_breaks_the_syntax_cannot_read_as_holding(self, toy: Path) -> None:
        (toy / "test_weak.py").write_text(WEAK_TEST + "\n", encoding="utf-8")
        args = [
            "--file",
            "toy.py",
            "--find",
            '    if name == "StructuredOutput":',
            "--replace",
            "",
            "--test",
            "test_weak.py",
        ]
        assert main(args) == EXIT_CANNOT_CHECK

    def test_a_revert_that_empties_the_selector_is_reported_as_cannot_check(
        self, toy: Path
    ) -> None:
        """pytest exits 5 for "no tests ran". A selector matching nothing must never be
        read as a passing baseline or as a caught revert."""
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        args = _revert_args(toy, "test_strong.py::TestNoSuchClass")
        assert main(args) == EXIT_CANNOT_CHECK

    def test_a_still_running_revert_is_still_reported_normally(self, toy: Path) -> None:
        """The refusal must not swallow the real verdicts.

        Deleting the `return True` leaves valid syntax and removes the behaviour, so the
        strong test must still be reported as holding — a tool that answers CANNOT CHECK
        to everything is as useless as one that answers HOLDS to everything.
        """
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        args = [
            "--file",
            "toy.py",
            "--find",
            "        return True\n",
            "--replace",
            "        return False\n",
            "--test",
            "test_strong.py",
        ]
        assert main(args) == EXIT_GUARD_HOLDS

    def test_the_child_run_is_not_subject_to_the_repositorys_coverage_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--no-cov` and the coverage-env scrub are both load-bearing and both invisible.

        Without `--no-cov` an in-repo baseline fails on the coverage floor and every guard
        reports exit 2. Without the env scrub the child inherits pytest-cov's subprocess
        instrumentation and corrupts the PARENT's measurement — which it did, making
        `cli.py` swing between 88% and 69% across identical runs until the floors started
        failing on a different module each time. A gate that fails at random gets switched
        off, which is the failure this whole tool is about.
        """
        captured: dict[str, Any] = {}

        def fake_run(argv: list[str], **kwargs: Any) -> Any:
            captured["argv"] = argv
            captured["env"] = kwargs.get("env", {})
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(verify_guard.subprocess, "run", fake_run)
        monkeypatch.setenv("COVERAGE_FILE", "/tmp/should-not-propagate")
        monkeypatch.setenv("COV_CORE_SOURCE", "creative_agent")
        verify_guard.run_tests(("some_test.py",))
        assert "--no-cov" in captured["argv"]
        assert not {"COVERAGE_FILE", "COV_CORE_SOURCE"} & set(captured["env"])

    def test_a_file_that_cannot_be_restored_is_reported_as_cannot_check(
        self, toy: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A silent failed restore leaves the operator committing a reverted tree."""
        (toy / "test_strong.py").write_text(STRONG_TEST + "\n", encoding="utf-8")
        original_restore = verify_guard.restore
        monkeypatch.setattr(verify_guard, "restore", lambda originals: None)
        try:
            assert main(_revert_args(toy, "test_strong.py")) == EXIT_CANNOT_CHECK
        finally:
            original_restore({toy / "toy.py": SOURCE + "\n"})
