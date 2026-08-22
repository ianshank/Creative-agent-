#!/usr/bin/env python3
"""Enforce per-package coverage floors from coverage.xml (DEC-F8).

The global gate lives in pyproject addopts; this catches the failure mode where high-line
pydantic model files subsidize an undertested harness. Floors are defined here as the CI
quality contract — changing one requires a decision-log entry.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

FLOORS: dict[str, float] = {
    "src/creative_agent/harness": 90.0,
    "src/creative_agent/models": 95.0,
    "src/creative_agent/cli.py": 85.0,
}


def main(path: str) -> int:
    tree = ET.parse(path)
    covered: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for cls in tree.iter("class"):
        filename = cls.get("filename", "")
        for prefix in FLOORS:
            if filename.startswith(prefix):
                for line in cls.iter("line"):
                    total[prefix] += 1
                    if int(line.get("hits", "0")) > 0:
                        covered[prefix] += 1
    failures = []
    for prefix, floor in FLOORS.items():
        if total[prefix] == 0:
            print(f"WARN: no measured lines under {prefix}")
            continue
        pct = 100.0 * covered[prefix] / total[prefix]
        status = "ok" if pct >= floor else "FAIL"
        print(f"{prefix}: {pct:.1f}% (floor {floor}%) {status}")
        if pct < floor:
            failures.append(prefix)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.xml"))
