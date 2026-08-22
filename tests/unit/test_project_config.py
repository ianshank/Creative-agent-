"""The build/quality configuration is itself a contract; assert it cannot rot silently.

Two of these guard failure modes we actually hit: the mutation-testing config was dead
(a string where a list was required, and a key the tool ignores), and the coverage omit
list is the one place a module can quietly leave the gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath

import pytest
import yaml
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


class TestCiWorkflowIsWired:
    """CI misconfiguration fails in ways that read as something else.

    The secret scan is the case that motivated these: the gitleaks action exits
    non-zero *before scanning* when `GITHUB_TOKEN` is absent on a `pull_request`
    event, which looks like a leak. It passed on every push and only failed on the
    first PR.
    """

    ROOT = PYPROJECT.parent

    @classmethod
    def _workflow(cls, name: str) -> dict:
        text = (cls.ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        assert isinstance(loaded, dict)
        return loaded

    @classmethod
    def _steps(cls, name: str) -> list[dict]:
        return [
            step
            for job in cls._workflow(name).get("jobs", {}).values()
            for step in job.get("steps", [])
        ]

    def test_gitleaks_receives_a_token_so_it_can_scan_pull_requests(self) -> None:
        steps = [s for s in self._steps("ci.yml") if "gitleaks-action" in str(s.get("uses", ""))]
        assert steps, "the secret-scan step vanished"
        for step in steps:
            assert "GITHUB_TOKEN" in step.get("env", {}), (
                "gitleaks-action fails before scanning on pull_request events without "
                "GITHUB_TOKEN, and the failure is indistinguishable from a real leak"
            )

    @pytest.mark.parametrize("workflow", ["ci.yml", "mutation.yml", "live.yml"])
    def test_actions_are_pinned_to_a_commit_sha(self, workflow: str) -> None:
        """A moving tag is an unreviewed dependency with write access to the build."""
        unpinned = [
            uses
            for step in self._steps(workflow)
            if (uses := str(step.get("uses", "")))
            and not re.search(r"@[0-9a-f]{40}$", uses)
            and not uses.startswith("./")
        ]
        assert not unpinned, f"{workflow} uses unpinned actions: {unpinned}"

    def test_every_job_running_make_installs_the_environment_first(self) -> None:
        """`make lint` without `make install` fails on a missing interpreter, not a defect."""
        for job_name, job in self._workflow("ci.yml").get("jobs", {}).items():
            runs = [str(step.get("run", "")) for step in job.get("steps", [])]
            make_calls = [r for r in runs if r.startswith("make ")]
            if not make_calls:
                continue
            assert any(r.startswith("make install") for r in make_calls), (
                f"job {job_name!r} runs {make_calls} without installing the environment"
            )


class TestContainerTestStageCanActuallyRunTheSuite:
    """The Dockerfile's test stage claims a green container means a green CI.

    That claim breaks silently: the suite audits the project's own configuration, so a
    file it reads that never reaches the build context makes the container *pass*
    vacuously rather than fail. Docker is not available in every environment this suite
    runs in, so these assert the invariant statically — from the Dockerfile and
    .dockerignore themselves — rather than by building.
    """

    ROOT = PYPROJECT.parent
    # Read through `default_claude_dir()`, not a literal, so the scan below cannot see it.
    IMPLICIT_DEPENDENCIES = frozenset({".claude"})

    @classmethod
    def _repo_root_dependencies(cls) -> set[str]:
        """Top-level context paths the suite reads, discovered from the tests themselves."""
        found = set(cls.IMPLICIT_DEPENDENCIES)
        for path in sorted((cls.ROOT / "tests").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for literal in re.findall(r"(?:ROOT|repo_root)\s*/\s*\"([^\"]+)\"", source):
                found.add(PurePosixPath(literal).parts[0])
        return found

    @classmethod
    def _context_copy_targets(cls) -> set[str]:
        """Every path copied from the build context, across all stages of the Dockerfile.

        `COPY --from=` reads another stage or image, not the context, so it is skipped;
        the test stage builds `FROM base`, so base's context copies count too.
        """
        dockerfile = (cls.ROOT / "Dockerfile").read_text(encoding="utf-8")
        copied: set[str] = set()
        for line in dockerfile.splitlines():
            stripped = line.strip()
            if not stripped.startswith("COPY ") or "--from=" in stripped:
                continue
            # The final argument is the destination; everything before it is a source.
            for source in stripped.split()[1:-1]:
                copied.add(PurePosixPath(source).parts[0])
        return copied

    def test_every_file_the_suite_reads_is_copied_into_the_image(self) -> None:
        missing = self._repo_root_dependencies() - self._context_copy_targets()
        assert not missing, (
            "the suite reads these but the Dockerfile never copies them, so "
            f"`make docker-test` would fail or pass vacuously: {sorted(missing)}"
        )

    def test_dockerignore_does_not_strip_what_the_dockerfile_copies(self) -> None:
        """An exclusion silently wins over a COPY: the build succeeds, the file is absent."""
        entries = [
            line.strip()
            for line in (self.ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(("#", "!"))
        ]
        for dependency in sorted(self._repo_root_dependencies()):
            assert dependency not in entries, (
                f".dockerignore excludes {dependency}, which the suite reads"
            )

    def test_test_stage_calls_the_gate_rather_than_restating_it(self) -> None:
        """A hand-copied command list is how the container silently stops matching CI."""
        dockerfile = (self.ROOT / "Dockerfile").read_text(encoding="utf-8")
        test_stage = dockerfile.split("AS test", 1)[1].split("AS runtime", 1)[0]
        assert 'CMD ["make", "gate"]' in test_stage, (
            "the test stage must invoke `make gate`, the single definition of the gate"
        )

    def test_the_scan_finds_the_dependencies_it_is_meant_to(self) -> None:
        """Guards the regex above: if it silently matched nothing, both tests would pass."""
        discovered = self._repo_root_dependencies()
        for expected in (".github", "Makefile", "README.md", ".claude"):
            assert expected in discovered, (
                f"the dependency scan no longer sees {expected}; the tests above are inert"
            )
