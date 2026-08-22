"""check_mutation_baseline.py: the mutation-testing kill-rate gate (roadmap item 6).

Guards the failure mode a "just compare the numbers" script tends to have: treating a
missing category as zero (silently permissive) instead of failing loudly, and gating on
counts rather than rates so a growing mutant population can't dilute a real regression.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_mutation_baseline import main


def write(path: Path, data: dict[str, int]) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


BASELINE = {
    "total": 452,
    "killed": 452,
    "survived": 0,
    "no_tests": 0,
    "suspicious": 0,
    "timeout": 0,
}


class TestPasses:
    def test_identical_to_baseline_passes(self, tmp_path: Path) -> None:
        stats = write(tmp_path / "stats.json", dict(BASELINE))
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        assert main(stats, baseline) == 0

    def test_more_killed_and_fewer_survived_than_baseline_passes(self, tmp_path: Path) -> None:
        """Improving on the baseline is never a failure."""
        looser_baseline = write(
            tmp_path / "baseline.json", {**BASELINE, "survived": 3, "killed": 449}
        )
        stats = write(tmp_path / "stats.json", dict(BASELINE))
        assert main(stats, looser_baseline) == 0

    def test_grown_mutant_population_with_no_new_survivors_passes(self, tmp_path: Path) -> None:
        """More code under test (higher total) is not itself a regression."""
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(tmp_path / "stats.json", {**BASELINE, "total": 500, "killed": 500})
        assert main(stats, baseline) == 0


class TestRegressionsFail:
    def test_new_survivor_fails(self, tmp_path: Path) -> None:
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(tmp_path / "stats.json", {**BASELINE, "survived": 1, "killed": 451})
        assert main(stats, baseline) == 1

    def test_new_no_tests_mutant_fails(self, tmp_path: Path) -> None:
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(tmp_path / "stats.json", {**BASELINE, "no_tests": 1, "killed": 451})
        assert main(stats, baseline) == 1

    def test_new_suspicious_mutant_fails(self, tmp_path: Path) -> None:
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(tmp_path / "stats.json", {**BASELINE, "suspicious": 1, "killed": 451})
        assert main(stats, baseline) == 1

    def test_new_timeout_fails(self, tmp_path: Path) -> None:
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(tmp_path / "stats.json", {**BASELINE, "timeout": 1, "killed": 451})
        assert main(stats, baseline) == 1


class TestMalformedInputFailsLoudly:
    def test_missing_stats_file_fails(self, tmp_path: Path) -> None:
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        assert main(str(tmp_path / "absent.json"), baseline) == 1

    def test_missing_baseline_file_fails(self, tmp_path: Path) -> None:
        stats = write(tmp_path / "stats.json", dict(BASELINE))
        assert main(stats, str(tmp_path / "absent.json")) == 1

    def test_category_missing_from_stats_fails_not_treated_as_zero(self, tmp_path: Path) -> None:
        """The regression this guards: a missing key silently reading as 0 survived."""
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        incomplete = dict(BASELINE)
        del incomplete["survived"]
        stats = write(tmp_path / "stats.json", incomplete)
        assert main(stats, baseline) == 1

    def test_category_missing_from_baseline_fails(self, tmp_path: Path) -> None:
        incomplete = dict(BASELINE)
        del incomplete["survived"]
        baseline = write(tmp_path / "baseline.json", incomplete)
        stats = write(tmp_path / "stats.json", dict(BASELINE))
        assert main(stats, baseline) == 1
