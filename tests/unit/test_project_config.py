"""The build/quality configuration is itself a contract; assert it cannot rot silently.

Two of these guard failure modes we actually hit: the mutation-testing config was dead
(a string where a list was required, and a key the tool ignores), and the coverage omit
list is the one place a module can quietly leave the gate.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from scripts.check_coverage_floors import APPROVED_OMITS, FLOORS

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def config() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


class TestMutationConfig:
    def test_source_paths_is_a_list_of_existing_files(self, config: dict) -> None:
        """mutmut 3.x iterates this value: a bare string yields characters, and the tool
        silently mutates nothing."""
        paths = config["tool"]["mutmut"]["source_paths"]
        assert isinstance(paths, list) and paths
        root = PYPROJECT.parent
        for entry in paths:
            assert (root / entry).is_file(), f"mutation target {entry} does not exist"

    def test_no_stale_two_x_keys(self, config: dict) -> None:
        mutmut = config["tool"]["mutmut"]
        assert "paths_to_mutate" not in mutmut, "renamed to source_paths in mutmut 3.x"
        assert "runner" not in mutmut, "not a mutmut 3.x key; it is silently ignored"

    def test_enforcement_core_is_covered(self, config: dict) -> None:
        targets = " ".join(config["tool"]["mutmut"]["source_paths"])
        for module in ("severity.py", "verification.py", "gates.py", "consistency.py"):
            assert module in targets


class TestCoverageConfig:
    def test_omit_list_matches_the_approved_set(self, config: dict) -> None:
        declared = set(config["tool"]["coverage"]["report"].get("omit", []))
        assert declared == APPROVED_OMITS, (
            "a new coverage omit needs a decision-log entry (DEC-F8) and an update to "
            "APPROVED_OMITS"
        )

    def test_branch_coverage_is_enabled(self, config: dict) -> None:
        assert config["tool"]["coverage"]["run"]["branch"] is True
        assert "--cov-branch" in config["tool"]["pytest"]["ini_options"]["addopts"]

    def test_coverage_floors_target_existing_paths(self) -> None:
        root = PYPROJECT.parent
        for prefix in FLOORS:
            assert (root / prefix).exists(), f"coverage floor prefix {prefix} does not exist"


class TestLintConfig:
    def test_datetime_calls_are_banned_in_source(self, config: dict) -> None:
        """Determinism (DEC-F8): all time comes from the injected Clock."""
        banned = config["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
        assert {"datetime.datetime.now", "datetime.datetime.utcnow", "datetime.date.today"} <= set(
            banned
        )

    def test_layering_contract_declared(self, config: dict) -> None:
        contracts = config["tool"]["importlinter"]["contracts"]
        forbidden = [c for c in contracts if c["type"] == "forbidden"]
        assert any(
            "creative_agent.agents" in c["forbidden_modules"]
            and "creative_agent.harness" in c["source_modules"]
            for c in forbidden
        )
