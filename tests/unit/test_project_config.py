"""The build/quality configuration is itself a contract; assert it cannot rot silently.

Two of these guard failure modes we actually hit: the mutation-testing config was dead
(a string where a list was required, and a key the tool ignores), and the coverage omit
list is the one place a module can quietly leave the gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from mutmut.configuration import Config
from scripts.check_coverage_floors import APPROVED_OMITS, FLOORS

from creative_agent.errors import ExitCode

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
        # Deprecated AND fatal: mutmut concatenates it with the list-valued selection key.
        assert "tests_dir" not in mutmut, "deprecated; breaks the run when both keys are set"

    def test_config_actually_loads(self) -> None:
        """The guard that would have caught every previous breakage of this config."""
        Config.ensure_loaded()
        assert Config.get().source_paths

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


class TestDocumentationIsHonest:
    """Docs rot silently. These assert the claims that would mislead a reader most —
    a referenced file that does not exist, or a command that no longer works."""

    ROOT = PYPROJECT.parent

    @pytest.mark.parametrize(
        "document",
        ["README.md", "CLAUDE.md", "CHANGELOG.md", "docs/roadmap.md", "docs/architecture.md"],
    )
    def test_referenced_documents_exist(self, document: str) -> None:
        assert (self.ROOT / document).is_file(), f"{document} is referenced but missing"

    def test_readme_make_targets_exist(self) -> None:
        """Every `make X` in the README must be a real target."""
        makefile = (self.ROOT / "Makefile").read_text(encoding="utf-8")
        targets = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        referenced = set(re.findall(r"\bmake ([a-z][\w-]*)", readme))
        missing = referenced - targets
        assert not missing, f"README references non-existent make targets: {sorted(missing)}"

    def test_gate_target_covers_every_ci_check(self) -> None:
        """`make gate` is documented as 'everything CI runs'; keep that true."""
        makefile = (self.ROOT / "Makefile").read_text(encoding="utf-8")
        gate_line = next(line for line in makefile.splitlines() if line.startswith("gate:"))
        gate_deps = set(gate_line.split(":")[1].split("##")[0].split())
        workflow = (self.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        ci_targets = set(re.findall(r"run: make ([a-z][\w-]*)", workflow))
        # install/install-unlocked are setup, not checks.
        checks = ci_targets - {"install", "install-unlocked"}
        assert checks <= gate_deps, (
            f"CI runs targets `make gate` omits: {sorted(checks - gate_deps)}"
        )

    def test_changelog_documents_the_exit_code_contract(self) -> None:
        changelog = (self.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        for code in ExitCode:
            assert str(int(code)) in changelog

    def test_dockerfile_runs_unprivileged(self) -> None:
        """The container reads untrusted documents; root would be a real exposure."""
        dockerfile = (self.ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "USER reviewer" in dockerfile
        assert "useradd" in dockerfile

    def test_dockerignore_excludes_local_state_and_secrets(self) -> None:
        ignored = (self.ROOT / ".dockerignore").read_text(encoding="utf-8")
        for entry in ("config/settings.yaml", ".env", ".git", ".venv"):
            assert entry in ignored, f"{entry} would be copied into the build context"

    def test_gitignore_protects_key_material(self) -> None:
        ignored = (self.ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("config/settings.yaml", ".env", "*.pem", "*.key"):
            assert entry in ignored

    def test_gitleaks_config_present_and_extends_defaults(self) -> None:
        config = (self.ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
        assert "useDefault = true" in config, "a bespoke ruleset would miss known secrets"
        assert "sk-ant-" in config, "the one credential this project uses is unpinned"
