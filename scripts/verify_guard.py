#!/usr/bin/env python3
"""Prove that a test actually guards the code it names, by reverting that code.

A test that still passes when the behaviour it describes is removed is not a test. This
repository has shipped that mistake repeatedly and at every level: a renderer escape whose
test grepped for an escape sequence, a glob check whose cases were the shapes the code
already caught, a budget test that watched a value computed by a neighbouring statement, a
permission-mode assertion that re-read its own fixture, an oracle invariant written to data
that had just been hand-edited. Every one of them was green. Five separate one-line
deletions in `pipeline.py` and `cli.py` each left the whole suite passing.

The check is mechanical, so it should be a command rather than a discipline:

    baseline   the named tests must PASS on the current tree
    revert     apply the substitution, which removes the guarded behaviour
    verify     the named tests must now FAIL — this is the whole point
    restore    put the file back, byte for byte, and confirm it

A guard that survives step three proves nothing about the code and should be rewritten to
assert an outcome rather than an intermediate value. That is the finding this tool exists
to produce.

Usage:

    python scripts/verify_guard.py \\
        --file src/creative_agent/harness/security.py \\
        --find-file /tmp/current.txt --replace-file /tmp/reverted.txt \\
        --test tests/unit/test_security.py::TestGlobScoping

    python scripts/verify_guard.py --spec guards/dec-f20.json

A spec is the same arguments as JSON, which is what to use when the substitution spans
several files or contains awkward quoting:

    {"name": "...", "tests": ["..."], "edits": [{"file": "...", "find": "...",
     "replace": "..."}]}

Exit codes, and the distinction between the last two is the point:

    0  the guard holds — the revert applied and the named tests failed
    1  the guard is WEAK — the behaviour was removed and the tests still passed
    2  the check could not run, so nothing was measured: the baseline was already red,
       the revert did not apply or was ambiguous, the reverted tree would not import or
       collect, the selector matched no test, or a file could not be restored

A failing baseline is 2, not 1. It used to be documented as 1, which was wrong in the
direction that matters: a shell script branching on the exit code would have read "your
tree is broken" as "your test is weak" and sent someone to rewrite a test that was fine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Distinct from 1 so a caller can tell "the guard is weak" from "the check never ran".
# Reporting a tool failure as a guard failure would send someone rewriting a test that is
# fine, which is the same class of error the tool exists to catch.
EXIT_GUARD_HOLDS = 0
EXIT_GUARD_WEAK = 1
EXIT_CANNOT_CHECK = 2

# pytest's own exit codes. Only ONE of these means "the named tests ran and failed", and
# that distinction is the whole verdict: this tool once reported GUARD HOLDS for a revert
# that left an `IndentationError`, because a collection error (2) is nonzero and nonzero
# was read as "the test caught it". The reassuring answer for a broken revert is the worst
# thing this tool can do — it is the instrument a whole branch of decisions rests on.
_PYTEST_OK = 0
_PYTEST_TESTS_FAILED = 1
_PYTEST_EXIT_MEANINGS = {
    2: "pytest could not collect the tests (a syntax or import error in the reverted tree)",
    3: "pytest hit an internal error",
    4: "pytest was called incorrectly",
    5: "pytest collected no tests — the selector matches nothing",
}


@dataclass(frozen=True)
class Edit:
    """One substitution that removes a guarded behaviour."""

    file: Path
    find: str
    replace: str


@dataclass(frozen=True)
class Guard:
    name: str
    tests: tuple[str, ...]
    edits: tuple[Edit, ...]


def load_spec(path: Path) -> Guard:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read guard spec {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"guard spec {path} must be a JSON object")
    edits = raw.get("edits") or []
    if not edits or not raw.get("tests"):
        raise SystemExit(f"guard spec {path} needs both 'edits' and 'tests'")
    return Guard(
        name=str(raw.get("name") or path.stem),
        tests=tuple(str(t) for t in raw["tests"]),
        edits=tuple(
            Edit(file=Path(e["file"]), find=str(e["find"]), replace=str(e["replace"]))
            for e in edits
        ),
    )


# Coverage plumbing the parent process may have set. pytest-cov instruments subprocesses
# through these, so a child inherits them and writes to the SAME data file — which
# corrupted the parent's measurement nondeterministically when this tool was exercised from
# inside a covered test run: `cli.py` swung between 88% and 69% across identical runs, and
# the coverage floors then failed on a different module each time. A gate that fails at
# random is a gate that gets switched off, which is the failure this whole tool is about.
# Clearing them is also just correct: `--no-cov` says this child's coverage is not wanted.
_COVERAGE_ENV_VARS = (
    "COV_CORE_SOURCE",
    "COV_CORE_CONFIG",
    "COV_CORE_DATAFILE",
    "COV_CORE_CONTEXT",
    "COVERAGE_FILE",
    "COVERAGE_PROCESS_START",
)


def _child_env() -> dict[str, str]:
    """The parent's environment without the coverage instrumentation it may carry."""
    return {k: v for k, v in os.environ.items() if k not in _COVERAGE_ENV_VARS}


def run_tests(tests: tuple[str, ...]) -> int:
    """pytest's exit code for the named tests.

    The code, not a boolean. A boolean collapses "the tests failed" together with "the
    tests could not run", and those are opposite verdicts: the first means the guard
    holds, the second means nothing was measured.

    `--no-cov` because a coverage floor failure would read as a test failure and make the
    revert look guarded when it is not — the tool would then report a guard that holds
    for a reason unrelated to the guard, which is the exact confusion it exists to remove.
    `-p no:randomly` keeps the two runs comparable.

    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-cov", "-p", "no:randomly", *tests],
        capture_output=True,
        text=True,
        check=False,
        env=_child_env(),
    )
    return completed.returncode


def apply(guard: Guard) -> dict[Path, str]:
    """Apply every edit, returning the original text of each file for restoration.

    All-or-nothing: if any edit does not match, everything already applied is rolled back
    before raising. A partially reverted tree would make the next run's baseline
    meaningless, and the operator would have no way to know.
    """
    originals: dict[Path, str] = {}
    try:
        for edit in guard.edits:
            text = edit.file.read_text(encoding="utf-8")
            originals.setdefault(edit.file, text)
            occurrences = text.count(edit.find)
            if occurrences == 0:
                raise LookupError(f"{edit.file}: 'find' text is not present")
            if occurrences > 1:
                raise LookupError(
                    f"{edit.file}: 'find' text appears {occurrences} times; make it unique "
                    "so the revert is the one you intend"
                )
            edit.file.write_text(text.replace(edit.find, edit.replace), encoding="utf-8")
    except (OSError, LookupError):
        restore(originals)
        raise
    return originals


def restore(originals: dict[Path, str]) -> None:
    """Put every file back, attempting all of them before reporting a failure.

    Best-effort across files deliberately: aborting on the first write error would leave
    the remaining files reverted for a reason that has nothing to do with them, and the
    operator would then have a partially reverted tree *and* an error naming only one
    file. Every failure is collected into the raised message instead.
    """
    failed: list[str] = []
    for path, text in originals.items():
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            failed.append(f"{path} ({exc})")
    if failed:
        raise OSError("could not restore: " + ", ".join(failed))


def restore_failures(originals: dict[Path, str]) -> list[str]:
    """Every file that is not back to its original content, and why.

    Reads defensively. Whatever broke a restore is likely to break the read that verifies
    it, and an `OSError` escaping here would end the process with exit code 1 — which is
    `EXIT_GUARD_WEAK`. A failed restore reported as a weak test is precisely the confusion
    these exit codes exist to prevent, and it would arrive with a modified working tree
    behind it.
    """
    failures: list[str] = []
    for path, text in originals.items():
        try:
            restored = path.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"{path} (cannot be read back: {exc})")
            continue
        if restored != text:
            failures.append(f"{path} (content differs from the original)")
    return failures


def _describe(code: int) -> str:
    """Why a pytest run that neither passed nor failed did neither."""
    return _PYTEST_EXIT_MEANINGS.get(code, f"pytest exited {code}")


def check(guard: Guard) -> int:
    print(f"guard: {guard.name}")
    print(f"  tests: {' '.join(guard.tests)}")

    baseline = run_tests(guard.tests)
    if baseline != _PYTEST_OK:
        if baseline == _PYTEST_TESTS_FAILED:
            print("  FAIL: the named tests do not pass on the current tree.")
            print("  Nothing can be concluded from a revert while the baseline is red.")
        else:
            print(f"  ERROR: {_describe(baseline)}.")
        return EXIT_CANNOT_CHECK
    print("  baseline: pass")

    try:
        originals = apply(guard)
    except (OSError, LookupError) as exc:
        print(f"  ERROR: cannot apply the revert: {exc}")
        return EXIT_CANNOT_CHECK

    restore_error: OSError | None = None
    try:
        reverted = run_tests(guard.tests)
    finally:
        # Restore in `finally` so a KeyboardInterrupt mid-run cannot leave the tree
        # reverted — but neither `return` nor raise from inside it. A `return` discards
        # the exception that sent us here; an escaping `OSError` ends the process with
        # exit code 1, which every caller reads as EXIT_GUARD_WEAK. So the message is
        # printed here, where it is seen even while another exception is propagating,
        # and the verdict is decided below.
        try:
            restore(originals)
        except OSError as exc:
            restore_error = exc
            print(f"  ERROR: {exc}")

    unrestored = restore_failures(originals)
    if restore_error is not None or unrestored:
        for failure in unrestored:
            print(f"  ERROR: not restored: {failure}")
        print("  Check these files before committing; the tree may still be reverted.")
        return EXIT_CANNOT_CHECK

    if reverted == _PYTEST_OK:
        print("  reverted: pass")
        print()
        print("  GUARD IS WEAK. The behaviour was removed and the tests still passed.")
        print("  The test is asserting something other than what its name claims —")
        print("  commonly a value a neighbouring statement computes, a property of its")
        print("  own fixture, or the subset of inputs the implementation already handles.")
        print("  Rewrite it to assert the outcome, then run this again.")
        return EXIT_GUARD_WEAK

    if reverted != _PYTEST_TESTS_FAILED:
        # The finding this tool exists to avoid giving. A revert that leaves the module
        # un-importable makes pytest exit 2, which is nonzero — and reading nonzero as
        # "the test caught it" certified a test that asserts its own input as a holding
        # guard. Say what happened instead: the revert has to be one the tree can still
        # run, or it measures nothing.
        print(f"  reverted: {_describe(reverted)}")
        print()
        print("  CANNOT CHECK. The revert did not produce a running test suite, so the")
        print("  tests never expressed an opinion. Narrow the revert until the tree still")
        print("  imports — remove a condition's effect, not its syntax.")
        return EXIT_CANNOT_CHECK

    print("  reverted: fail")
    print("  GUARD HOLDS: removing the behaviour fails the test.")
    return EXIT_GUARD_HOLDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spec", type=Path, help="JSON guard spec")
    parser.add_argument("--file", type=Path, help="file to revert (single-edit form)")
    parser.add_argument("--find", help="exact text to replace")
    parser.add_argument("--replace", default="", help="replacement text (default: remove)")
    parser.add_argument("--find-file", type=Path, help="read --find from this file")
    parser.add_argument("--replace-file", type=Path, help="read --replace from this file")
    parser.add_argument("--test", action="append", default=[], help="pytest selector (repeatable)")
    parser.add_argument("--name", default="", help="label for the report")
    return parser


def guard_from_args(args: argparse.Namespace) -> Guard:
    if args.spec:
        return load_spec(args.spec)
    if not args.file or not args.test:
        raise SystemExit("need --spec, or --file with at least one --test")
    find = args.find_file.read_text(encoding="utf-8") if args.find_file else args.find
    if not find:
        raise SystemExit("need --find or --find-file")
    replace = args.replace_file.read_text(encoding="utf-8") if args.replace_file else args.replace
    return Guard(
        name=args.name or f"{args.file}",
        tests=tuple(args.test),
        edits=(Edit(file=args.file, find=find, replace=replace),),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return check(guard_from_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
