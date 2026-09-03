#!/usr/bin/env python3
"""Enforce the mutation-testing kill-rate baseline (roadmap item 6).

`mutmut run` followed by `mutmut export-cicd-stats` produces a JSON summary of
killed/survived/no_tests/suspicious/timeout counts. This script compares that summary
against `docs/mutation-baseline.json` and fails if any non-killed category regresses.

Failure modes this script must not have:
- a category missing from either file must fail loudly, not be treated as zero;
- the check compares counts, not rates, so growing the mutant population (a new function
  in the enforcement core) can't silently dilute a real regression into a passing rate;
- a collapsed mutant population must fail: gating only on the non-killed categories means
  a run that generated no mutants at all reports 0 survived / 0 no_tests / 0 suspicious /
  0 timeout and passes, so a broken `source_paths`, a failed sandbox copy, or a mutmut
  upgrade that silently mutates nothing would read as a perfect kill rate.

Not 100% by construction: mutmut can generate an equivalent mutant (semantically
unreachable, e.g. a boundary the type system already forecloses) that no test could ever
kill. The baseline file is where that gets recorded, with a reason — not by loosening this
script's comparison.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Categories where a higher count than baseline is a regression. "killed" is reported but
# not gated: a higher killed count alone is never bad. "total" is gated separately, and
# only downwards — it can grow freely (new code under test) but must not collapse.
REGRESSION_CATEGORIES = ("survived", "no_tests", "suspicious", "timeout")

# The category naming how many mutants the run actually generated.
POPULATION_CATEGORY = "total"

# How far the mutant population may shrink below the baseline before this script calls it
# a collapse rather than ordinary churn. Deleting a branch in the enforcement core
# legitimately removes its mutants, so a small drop is expected; a large one means the run
# mutated a different — or empty — set of files, which every gated category above would
# report as a flawless zero. Expressed as a fraction of the baseline `total` so the
# tolerance scales with the code under mutation instead of needing an edit each time the
# core grows.
MAX_POPULATION_SHRINK_RATIO = 0.05


def _load(path: Path, label: str) -> dict[str, int] | None:
    if not path.is_file():
        print(f"FAIL: {label} not found at {path}", file=sys.stderr)
        return None
    with path.open(encoding="utf-8") as handle:
        result: dict[str, int] = json.load(handle)
        return result


def _population_failures(stats: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Fail when the mutant population collapsed, not only when a mutant survived.

    Every other check here reads a non-killed count and asks whether it grew. A run that
    produced no mutants satisfies all of them, so the population itself has to be gated.
    """
    for source, label in ((stats, "stats"), (baseline, "baseline")):
        if POPULATION_CATEGORY not in source:
            return [f"{label} file is missing the {POPULATION_CATEGORY!r} category"]

    current = stats[POPULATION_CATEGORY]
    expected = baseline[POPULATION_CATEGORY]
    minimum = math.floor(expected * (1.0 - MAX_POPULATION_SHRINK_RATIO))
    print(f"{POPULATION_CATEGORY}: {current} (baseline {expected}, minimum {minimum})")

    if current <= 0:
        return [
            "the mutant population collapsed to "
            f"{current}: no mutant ran, so every category above is zero for the wrong "
            "reason — check source_paths, the sandbox copy, and the mutmut version"
        ]
    if current < minimum:
        return [
            f"the mutant population shrank to {current} from a baseline of {expected}, "
            f"below the {minimum} floor "
            f"({MAX_POPULATION_SHRINK_RATIO:.0%} tolerance) — the run mutated less code "
            "than the baseline measured, so an unchanged kill count proves less than it "
            "appears to; re-baseline deliberately if the shrink is real"
        ]
    return []


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

    failures.extend(_population_failures(stats, baseline))

    print(f"killed: {stats.get('killed', '?')}")

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
