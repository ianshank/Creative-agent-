#!/usr/bin/env python3
"""Enforce per-target coverage floors from coverage.xml (DEC-F8).

The global `--cov-fail-under` gate is an average: it lets a well-tested renderer
subsidize an undertested pipeline. These floors are per-package AND per-module for the
files where a silent gap matters most, and they check BRANCH coverage as well as lines —
a `min()` with an inverted comparison reads 100% on lines and fails on branches.

Failure modes this script must not have (each is enforced below):
- a prefix that matches nothing must FAIL, not warn: a directory rename would otherwise
  silently disable the gate;
- the coverage `omit` list must match the approved set, so a module cannot be quietly
  excluded from the gate it is supposed to pass.
"""

from __future__ import annotations

import sys
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# target prefix -> (line floor %, branch floor %)
FLOORS: dict[str, tuple[float, float]] = {
    "src/creative_agent/harness": (90.0, 80.0),
    "src/creative_agent/models": (95.0, 85.0),
    "src/creative_agent/cli.py": (85.0, 70.0),
    # The enforcement core: a softened rule here is the worst silent failure the harness
    # can have, so these carry their own floors rather than hiding in the package average.
    "src/creative_agent/harness/pipeline.py": (88.0, 75.0),
    "src/creative_agent/harness/severity.py": (95.0, 90.0),
    "src/creative_agent/harness/verification.py": (95.0, 90.0),
}

# Modules deliberately outside the gate. Changing this set requires a decision-log entry
# (DEC-F8); the test suite asserts pyproject matches it.
APPROVED_OMITS: set[str] = {"*/harness/llm/claude_sdk.py"}


def approved_omits_match(pyproject: Path) -> list[str]:
    with pyproject.open("rb") as handle:
        config = tomllib.load(handle)
    declared = set(config["tool"]["coverage"]["report"].get("omit", []))
    if declared != APPROVED_OMITS:
        return [
            f"coverage omit list {sorted(declared)} does not match the approved set "
            f"{sorted(APPROVED_OMITS)}; a new omit needs a decision-log entry (DEC-F8)"
        ]
    return []


def measure(path: Path) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Return per-prefix [covered, total] for lines and for branches."""
    tree = ET.parse(path)
    lines: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    branches: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for cls in tree.iter("class"):
        filename = cls.get("filename", "")
        for prefix in FLOORS:
            if not filename.startswith(prefix):
                continue
            for line in cls.iter("line"):
                lines[prefix][1] += 1
                if int(line.get("hits", "0")) > 0:
                    lines[prefix][0] += 1
                if line.get("branch") == "true":
                    # condition-coverage looks like "50% (1/2)"
                    condition = line.get("condition-coverage", "")
                    if "(" in condition:
                        taken, total = condition.split("(")[1].rstrip(")").split("/")
                        branches[prefix][0] += int(taken)
                        branches[prefix][1] += int(total)
    return lines, branches


def main(coverage_path: str, pyproject_path: str) -> int:
    failures: list[str] = approved_omits_match(Path(pyproject_path))
    lines, branches = measure(Path(coverage_path))

    for prefix, (line_floor, branch_floor) in FLOORS.items():
        covered, total = lines[prefix]
        if total == 0:
            failures.append(
                f"{prefix}: no measured lines — the floor matched nothing, so the gate "
                "is not actually running (renamed path?)"
            )
            continue
        line_pct = 100.0 * covered / total
        branch_covered, branch_total = branches[prefix]
        branch_pct = 100.0 * branch_covered / branch_total if branch_total else 100.0
        line_ok = line_pct >= line_floor
        branch_ok = branch_pct >= branch_floor
        status = "ok" if line_ok and branch_ok else "FAIL"
        print(
            f"{prefix}: lines {line_pct:.1f}% (floor {line_floor}%) "
            f"branches {branch_pct:.1f}% (floor {branch_floor}%) {status}"
        )
        if not line_ok:
            failures.append(f"{prefix}: line coverage {line_pct:.1f}% < {line_floor}%")
        if not branch_ok:
            failures.append(f"{prefix}: branch coverage {branch_pct:.1f}% < {branch_floor}%")

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "coverage.xml",
            sys.argv[2] if len(sys.argv) > 2 else "pyproject.toml",
        )
    )
