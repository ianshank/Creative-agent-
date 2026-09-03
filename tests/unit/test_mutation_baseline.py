"""check_mutation_baseline.py: the mutation-testing kill-rate gate (roadmap item 6).

Guards the failure mode a "just compare the numbers" script tends to have: treating a
missing category as zero (silently permissive) instead of failing loudly, and gating on
counts rather than rates so a growing mutant population can't dilute a real regression.

`TestPopulationCollapseFailsLoudly` guards the third one: the gated categories are all
*non-killed* counts, so a run that generated no mutants at all reports zero for every one
of them and passes. The number of mutants must therefore be gated on its own.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from scripts.check_mutation_baseline import MAX_POPULATION_SHRINK_RATIO, main


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

# The smallest population the gate accepts for the baseline above. Derived from the
# script's own tolerance rather than restated, so tightening the constant tightens the
# tests with it instead of leaving them asserting a stale number.
MINIMUM_POPULATION = math.floor(BASELINE["total"] * (1.0 - MAX_POPULATION_SHRINK_RATIO))


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

    def test_a_shrink_within_tolerance_passes(self, tmp_path: Path) -> None:
        """Ordinary churn — a deleted branch takes its mutants with it — is not a
        collapse. Without this the population guard would turn every refactor of the
        enforcement core into a red weekly job and get disabled."""
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        stats = write(
            tmp_path / "stats.json",
            {**BASELINE, "total": MINIMUM_POPULATION, "killed": MINIMUM_POPULATION},
        )
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


class TestPopulationCollapseFailsLoudly:
    """A mutation run that mutated nothing must fail, not report a perfect score.

    Every gated category counts mutants that were *not* killed, so an all-zero stats file
    — the exact shape `mutmut export-cicd-stats` produces when `source_paths` stops
    resolving, the sandbox copy fails, or an upgrade changes the discovery rules —
    satisfies all of them and exits 0. The kill rate looks flawless because nothing ran.
    """

    def test_an_all_zero_stats_file_fails(self, tmp_path: Path) -> None:
        """The literal shape of the defect: zero mutants, zero of everything else."""
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        collapsed = write(tmp_path / "stats.json", dict.fromkeys(BASELINE, 0))
        assert main(collapsed, baseline) == 1

    def test_a_shrunken_but_nonzero_population_fails(self, tmp_path: Path) -> None:
        """Half the core silently dropped from mutation is as blind as all of it.

        A nonzero total defeats a bare `total > 0` check, so the gate compares against the
        baseline population and not merely against zero.
        """
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        shrunken = MINIMUM_POPULATION - 1
        stats = write(tmp_path / "stats.json", {**BASELINE, "total": shrunken, "killed": shrunken})
        assert shrunken > 0, "the fixture must not accidentally test the zero case"
        assert main(stats, baseline) == 1

    def test_total_missing_from_stats_fails(self, tmp_path: Path) -> None:
        """A missing population key must fail loudly, not default to a passing number."""
        baseline = write(tmp_path / "baseline.json", dict(BASELINE))
        incomplete = dict(BASELINE)
        del incomplete["total"]
        assert main(write(tmp_path / "stats.json", incomplete), baseline) == 1

    def test_total_missing_from_baseline_fails(self, tmp_path: Path) -> None:
        """Same, from the other side: with no recorded population there is nothing to
        compare against, and silently skipping the check restores the original defect."""
        incomplete = dict(BASELINE)
        del incomplete["total"]
        baseline = write(tmp_path / "baseline.json", incomplete)
        stats = write(tmp_path / "stats.json", dict(BASELINE))
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
