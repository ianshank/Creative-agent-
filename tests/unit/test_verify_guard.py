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

import pytest
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
