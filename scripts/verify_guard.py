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

Exit codes: 0 the guard holds, 1 the guard does not hold (or the baseline was already
failing), 2 the tool could not run the check — a spec that does not apply, a file it
cannot restore.
"""

from __future__ import annotations

import argparse
import json
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


def run_tests(tests: tuple[str, ...]) -> bool:
    """True when the named tests pass.

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
    )
    return completed.returncode == 0


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
    for path, text in originals.items():
        path.write_text(text, encoding="utf-8")


def check(guard: Guard) -> int:
    print(f"guard: {guard.name}")
    print(f"  tests: {' '.join(guard.tests)}")

    if not run_tests(guard.tests):
        print("  FAIL: the named tests do not pass on the current tree.")
        print("  Nothing can be concluded from a revert while the baseline is red.")
        return EXIT_CANNOT_CHECK
    print("  baseline: pass")

    try:
        originals = apply(guard)
    except (OSError, LookupError) as exc:
        print(f"  ERROR: cannot apply the revert: {exc}")
        return EXIT_CANNOT_CHECK

    try:
        still_passing = run_tests(guard.tests)
    finally:
        # Restore in `finally` so a KeyboardInterrupt mid-run cannot leave the tree
        # reverted, but *verify* the restore outside it: a `return` inside `finally`
        # discards the exception that sent us here, so the operator would be told the
        # file was not restored and never learn why the run stopped.
        restore(originals)
    unrestored = [p for p, text in originals.items() if p.read_text(encoding="utf-8") != text]
    if unrestored:
        names = ", ".join(str(p) for p in unrestored)
        print(f"  ERROR: not restored: {names}; check these before committing.")
        return EXIT_CANNOT_CHECK
    print("  reverted: " + ("pass" if still_passing else "fail"))

    if still_passing:
        print()
        print("  GUARD IS WEAK. The behaviour was removed and the tests still passed.")
        print("  The test is asserting something other than what its name claims —")
        print("  commonly a value a neighbouring statement computes, a property of its")
        print("  own fixture, or the subset of inputs the implementation already handles.")
        print("  Rewrite it to assert the outcome, then run this again.")
        return EXIT_GUARD_WEAK

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
