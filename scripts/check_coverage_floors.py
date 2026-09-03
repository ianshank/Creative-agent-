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
#
# A floor is a ratchet, not an aspiration: it is set just under what the suite currently
# achieves, so a change that removes coverage fails rather than spending headroom someone
# else earned. Leaving a floor far below actual coverage is the same defect as having no
# floor — it permits a silent regression of everything in between, which is exactly how
# `verification.py` came to have two branches (the two tool-honesty paths) that could be
# deleted with the suite still green while its floor read a comfortable 95/90.
#
# Raising a floor is routine and needs no ceremony. LOWERING one needs a decision-log
# entry saying which coverage was given up and why.
FLOORS: dict[str, tuple[float, float]] = {
    "src/creative_agent/harness": (96.0, 92.0),
    "src/creative_agent/models": (99.0, 99.0),
    "src/creative_agent/cli.py": (86.0, 88.0),
    # The enforcement core: a softened rule here is the worst silent failure the harness
    # can have, so these carry their own floors rather than hiding in the package average.
    # 89, not 90, and it moved DOWN — see DEC-F42. Extracting `ModeResolver` and
    # `RunBudget` moved ~100 fully-covered lines out of this module and into two modules
    # that carry 100/100 floors of their own, so the branch ratio of what remains fell
    # while total enforcement rose. The two branches still uncovered here are a defensive
    # `raise` that no path reaches and the repair loop exhausting without a break.
    "src/creative_agent/harness/pipeline.py": (99.0, 89.0),
    "src/creative_agent/harness/severity.py": (100.0, 100.0),
    "src/creative_agent/harness/verification.py": (100.0, 100.0),
    # The security surface: every one of this branch's live defects was here.
    "src/creative_agent/harness/security.py": (97.0, 96.0),
    "src/creative_agent/harness/canonical.py": (100.0, 100.0),
    "src/creative_agent/harness/artifact.py": (100.0, 100.0),
    "src/creative_agent/harness/classification.py": (100.0, 100.0),
    "src/creative_agent/harness/budget.py": (100.0, 100.0),
    "src/creative_agent/harness/exitcodes.py": (100.0, 100.0),
    "src/creative_agent/harness/policy.py": (100.0, 100.0),
    "src/creative_agent/harness/asset_references.py": (100.0, 100.0),
    "src/creative_agent/harness/filelock.py": (95.0, 85.0),
    # The composition root. It is where `protocol_tools`, `permission_mode`, the fetch
    # allowlist and every budget are defined, and it fell under NO floor: at 98 statements
    # against a ~2700-statement total it could have regressed to zero coverage with both
    # this script and the global 90% gate still green.
    "src/creative_agent/config.py": (98.0, 90.0),
    "src/creative_agent/errors.py": (100.0, 100.0),
    # The one shipped agent plugin. The framework claim is that a new corpus is a YAML
    # file and not new code, which makes this module the reference implementation of the
    # plugin contract — the thing a second distribution would copy.
    "src/creative_agent/agents": (100.0, 100.0),
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
