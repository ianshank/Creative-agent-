#!/usr/bin/env python3
"""Enforce the mutation-testing kill-rate baseline (roadmap item 6).

`mutmut run` followed by `mutmut export-cicd-stats` produces a JSON summary of
killed/survived/no_tests/suspicious/timeout counts. This script compares that summary
against `docs/mutation-baseline.json` and fails if any non-killed category regresses.

Failure modes this script must not have:
- a category missing from either file must fail loudly, not be treated as zero;
- the check compares counts, not rates, so growing the mutant population (a new function
  in the enforcement core) can't silently dilute a real regression into a passing rate.

Not 100% by construction: mutmut can generate an equivalent mutant (semantically
unreachable, e.g. a boundary the type system already forecloses) that no test could ever
kill. The baseline file is where that gets recorded, with a reason — not by loosening this
script's comparison.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Categories where a higher count than baseline is a regression. "killed" and "total" are
# reported but not gated here — total can grow (new code under test) without that being a
# problem, and a higher killed count alone is never bad.
REGRESSION_CATEGORIES = ("survived", "no_tests", "suspicious", "timeout")


def _load(path: Path, label: str) -> dict[str, int] | None:
    if not path.is_file():
        print(f"FAIL: {label} not found at {path}", file=sys.stderr)
        return None
    with path.open(encoding="utf-8") as handle:
        result: dict[str, int] = json.load(handle)
        return result


def main(stats_path: str, baseline_path: str) -> int:
    stats = _load(Path(stats_path), "mutation stats")
    baseline = _load(Path(baseline_path), "mutation baseline")
    if stats is None or baseline is None:
        return 1

    failures: list[str] = []
    for category in REGRESSION_CATEGORIES:
        if category not in stats:
            failures.append(f"stats file is missing the {category!r} category")
            continue
        if category not in baseline:
            failures.append(f"baseline file is missing the {category!r} category")
            continue
        current = stats[category]
        allowed = baseline[category]
        status = "ok" if current <= allowed else "FAIL"
        print(f"{category}: {current} (baseline {allowed}) {status}")
        if current > allowed:
            failures.append(
                f"{category} regressed: {current} now vs {allowed} in the baseline — "
                "a mutant that used to be killed now is not"
            )

    print(f"killed: {stats.get('killed', '?')}  total: {stats.get('total', '?')}")

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "mutants/mutmut-cicd-stats.json",
            sys.argv[2] if len(sys.argv) > 2 else "docs/mutation-baseline.json",
        )
    )
