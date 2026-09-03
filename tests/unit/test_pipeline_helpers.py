"""Unit tests for pipeline-adjacent helpers: artifact identity, fake client, registry,
prompt assembly, renderer determinism."""

from pathlib import Path

import pytest

from creative_agent.errors import ConfigError, LLMTransportError
from creative_agent.harness.artifact import (
    MAX_ARTIFACT_ID_LENGTH,
    content_sha256,
    read_artifact,
    resolve_artifact_id,
    validate_artifact_id,
)
from creative_agent.harness.llm.base import AssembledPrompt, CallKind, RawLLMResult
from creative_agent.harness.llm.fake import FakeLLMClient, script_key
from creative_agent.harness.prompting import PromptAssembler, output_model_for
from creative_agent.harness.registry import AgentRegistry
from creative_agent.harness.rendering import OutputRenderer
from creative_agent.models.findings import Severity
from creative_agent.models.output import ReviewReport, Verdict
from creative_agent.models.sweeps import ClassifyResult
from tests.factories import make_finding, make_key, make_oracle


class TestArtifactIdentity:
    def test_override_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "My Design.md"
        assert resolve_artifact_id(path, "text", "explicit-id") == "explicit-id"

    def test_front_matter_review_id(self, tmp_path: Path) -> None:
        text = "---\nreview-id: rl-blueprint-7\n---\n# Doc\n"
        assert resolve_artifact_id(tmp_path / "x.md", text, None) == "rl-blueprint-7"

    def test_filename_slug_fallback_normalizes(self, tmp_path: Path) -> None:
        path = tmp_path / "My RL Design (v2).md"
        assert resolve_artifact_id(path, "no front matter", None) == "my-rl-design-v2"

    def test_sha_ignores_line_endings(self) -> None:
        assert content_sha256("a\r\nb\r") == content_sha256("a\nb\n")

    def test_sha_ignores_bom(self) -> None:
        assert content_sha256("\ufeff# Doc\n") == content_sha256("# Doc\n")

    @pytest.mark.parametrize(
        "text",
        [
            "\ufeff---\nreview-id: rl-blueprint-7\n---\n",
            "---\r\nreview-id: rl-blueprint-7\r\n---\r\n",
            "\ufeff---\r\nreview-id: rl-blueprint-7\r\n---\r\n",
        ],
    )
    def test_bom_and_crlf_do_not_defeat_front_matter(self, text: str, tmp_path: Path) -> None:
        """A Windows checkout of the same document must keep its cycle history."""
        assert resolve_artifact_id(tmp_path / "my-doc.md", text, None) == "rl-blueprint-7"

    def test_front_matter_without_review_id_falls_back_to_slug(self, tmp_path: Path) -> None:
        text = "---\ntitle: Something Else\n---\n"
        assert resolve_artifact_id(tmp_path / "my-doc.md", text, None) == "my-doc"

    def test_unslugifiable_filename_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot derive"):
            resolve_artifact_id(tmp_path / "---.md", "no front matter", None)


class TestReadArtifact:
    def test_missing_file_is_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot read artifact"):
            read_artifact(tmp_path / "absent.md", 1000)

    def test_oversized_artifact_rejected_before_reading(self, tmp_path: Path) -> None:
        path = tmp_path / "big.md"
        path.write_text("x" * 500, encoding="utf-8")
        with pytest.raises(ConfigError, match="exceeds 100 bytes"):
            read_artifact(path, 100)

    def test_invalid_utf8_is_replaced_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "binary.md"
        path.write_bytes(b"valid \xff\xfe bytes")
        assert "valid" in read_artifact(path, 1000)


class TestArtifactIdSafety:
    """The id becomes a path segment, so every source of it must be validated."""

    @pytest.mark.parametrize(
        "malicious",
        [
            "../../etc/passwd",
            "..",
            "a/b",
            "a\\b",
            "/absolute",
            ".hidden",
            "-leading-dash",
            "with space",
            "nul\x00byte",
            "   ",
            "x" * 129,
        ],
    )
    def test_malicious_override_rejected(self, malicious: str, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="invalid artifact id"):
            resolve_artifact_id(tmp_path / "doc.md", "text", malicious)

    def test_empty_override_falls_back_to_filename(self, tmp_path: Path) -> None:
        """An empty flag value means 'not provided', not an invalid id."""
        assert resolve_artifact_id(tmp_path / "doc.md", "text", "") == "doc"

    def test_traversal_in_front_matter_rejected(self, tmp_path: Path) -> None:
        """The front matter comes from the untrusted artifact."""
        text = "---\nreview-id: a..b\n---\n"
        with pytest.raises(ConfigError, match="invalid artifact id"):
            resolve_artifact_id(tmp_path / "doc.md", text, None)

    def test_safe_override_accepted(self, tmp_path: Path) -> None:
        assert resolve_artifact_id(tmp_path / "doc.md", "t", "rl_blueprint-7.v2") == (
            "rl_blueprint-7.v2"
        )

    def test_long_filename_is_truncated_not_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / (("very-long-name-" * 20) + ".md")
        resolved = resolve_artifact_id(path, "t", None)
        assert 0 < len(resolved) <= MAX_ARTIFACT_ID_LENGTH

    def test_validate_helper_names_its_source(self) -> None:
        with pytest.raises(ConfigError, match="from --artifact-id"):
            validate_artifact_id("../x", source="--artifact-id")


class TestFakeClient:
    def _prompt(self, kind: CallKind = CallKind.CLASSIFY, ref: str = "") -> AssembledPrompt:
        return AssembledPrompt(
            kind=kind, ref=ref, system="s", user="u", output_schema={"type": "object"}
        )

    @pytest.mark.asyncio()
    async def test_keyed_by_kind_and_ref(self) -> None:
        result = RawLLMResult(kind=CallKind.ROW, payload={"row_id": "D1", "verdict": "hit"})
        fake = FakeLLMClient({script_key(CallKind.ROW, "D1"): [result]})
        out = await fake.generate(self._prompt(CallKind.ROW, "D1"))
        assert out.payload["row_id"] == "D1"
        fake.assert_consumed()

    @pytest.mark.asyncio()
    async def test_wildcard_fallback(self) -> None:
        result = RawLLMResult(kind=CallKind.ROW, payload={})
        fake = FakeLLMClient({script_key(CallKind.ROW, "*"): [result]})
        assert await fake.generate(self._prompt(CallKind.ROW, "D9")) is result

    @pytest.mark.asyncio()
    async def test_missing_script_is_typed_error(self) -> None:
        fake = FakeLLMClient({})
        with pytest.raises(LLMTransportError, match="no scripted response"):
            await fake.generate(self._prompt())

    def test_unconsumed_scripts_fail(self) -> None:
        fake = FakeLLMClient(
            {script_key(CallKind.CLASSIFY): [RawLLMResult(kind=CallKind.CLASSIFY, payload={})]}
        )
        with pytest.raises(AssertionError, match="unconsumed"):
            fake.assert_consumed()


class TestRegistry:
    def test_register_and_get(self) -> None:
        registry = AgentRegistry()
        sentinel = object()
        registry.register("x", lambda: sentinel)  # type: ignore[arg-type,return-value]
        assert registry.get("x") is sentinel
        assert registry.names() == ["x"]

    def test_duplicate_and_unknown_are_config_errors(self) -> None:
        registry = AgentRegistry()
        registry.register("x", lambda: None)  # type: ignore[arg-type,return-value]
        with pytest.raises(ConfigError, match="already registered"):
            registry.register("x", lambda: None)  # type: ignore[arg-type,return-value]
        with pytest.raises(ConfigError, match="unknown agent"):
            registry.get("y")


class TestPromptAssembler:
    def _assembler(self) -> PromptAssembler:
        return PromptAssembler([], "default")

    def test_schema_is_generated_from_model(self) -> None:
        oracle = make_oracle()
        prompt = self._assembler().assemble(
            CallKind.CLASSIFY,
            allowed_tools=["Read"],
            fetch_domain_allowlist=["arxiv.org"],
            context={
                "oracle": oracle,
                "artifact": "text",
                "fetch_domains": ["arxiv.org"],
                "prior_keys": [],
                "repair_defects": [],
            },
        )
        assert prompt.output_schema == ClassifyResult.model_json_schema()
        assert "conformance" in prompt.user

    def test_system_prompt_carries_hard_rules_and_marker(self) -> None:
        oracle = make_oracle()
        prompt = self._assembler().assemble(
            CallKind.SYNTHESIS,
            allowed_tools=[],
            fetch_domain_allowlist=[],
            context={
                "oracle": oracle,
                "artifact": "text",
                "fetch_domains": ["arxiv.org"],
                "prior_keys": [],
                "repair_defects": [],
                "findings_summary": [],
                "mode": "advisory",
                "artifact_class": "architecture_design",
            },
        )
        assert "Never invent positions" in prompt.system
        assert oracle.protocol.unverified_marker in prompt.system

    def test_repair_defects_rendered(self) -> None:
        oracle = make_oracle()
        prompt = self._assembler().assemble(
            CallKind.CLASSIFY,
            allowed_tools=[],
            fetch_domain_allowlist=[],
            context={
                "oracle": oracle,
                "artifact": "text",
                "fetch_domains": [],
                "prior_keys": [],
                "repair_defects": ["fix the D2 entry"],
            },
        )
        assert "REPAIR PASS" in prompt.system and "fix the D2 entry" in prompt.system

    def test_every_call_kind_has_a_template(self) -> None:
        oracle = make_oracle()
        base = {
            "oracle": oracle,
            "artifact": "text",
            "fetch_domains": [],
            "prior_keys": [],
            "repair_defects": [],
            "mode": "advisory",
            "artifact_class": "architecture_design",
            "findings_summary": [],
            "row": oracle.rows[0],
            "row_rendered": "row text",
        }
        for kind in CallKind:
            prompt = self._assembler().assemble(
                kind, allowed_tools=[], fetch_domain_allowlist=[], context=base
            )
            assert prompt.output_schema == output_model_for(kind).model_json_schema()


class TestRendererDeterminism:
    def _report(self) -> ReviewReport:
        return ReviewReport(
            artifact_id="doc",
            oracle_id="mini",
            oracle_version="1.0",
            cycle=1,
            verdict=Verdict(mode="conformance", confidence="Likely", headline="Broken"),
            findings=[
                make_finding(
                    finding_id="F2",
                    key=make_key("D1", "b-minor"),
                    severity=Severity.MINOR,
                    original_severity=Severity.MINOR,
                ),
                make_finding(
                    finding_id="F1",
                    key=make_key("D1", "a-blocker"),
                    severity=Severity.BLOCKER,
                    original_severity=Severity.BLOCKER,
                ),
            ],
        )

    def test_findings_sorted_severity_desc_then_key(self) -> None:
        rendered = OutputRenderer("[Unverified]").render(self._report())
        assert rendered.index("F1") < rendered.index("F2")

    def test_render_is_stable(self) -> None:
        renderer = OutputRenderer("[Unverified]")
        assert renderer.render(self._report()) == renderer.render(self._report())

    def test_capped_severity_shows_original(self) -> None:
        report = self._report()
        report = report.model_copy(
            update={
                "findings": [
                    make_finding(
                        severity=Severity.INFO,
                        original_severity=Severity.BLOCKER,
                        cap_reason="advisory",
                    )
                ]
            }
        )
        rendered = OutputRenderer("[Unverified]").render(report)
        assert "Info (was Blocker)" in rendered


class TestArtifactPathIsUntrusted:
    """The artifact *path* is as untrusted as its contents (plan item 1.7, DEC-F9).

    The documented `--artifact-repo` flow reviews a checked-out worktree, and git carries
    symlinks, so the reviewed repository chooses where `docs/design.md` actually points.
    Nothing checked `S_ISREG` or containment: Typer's `dir_okay=False` rejects directories
    and nothing else.
    """

    def test_a_character_device_is_refused_before_it_is_read(self, tmp_path: Path) -> None:
        """`stat().st_size` reports 0 for /dev/zero, so the size cap passed and the read
        that followed was unbounded — a plausible out-of-memory from a one-line symlink."""
        link = tmp_path / "design.md"
        link.symlink_to("/dev/zero")
        with pytest.raises(ConfigError, match="not a regular file"):
            read_artifact(link, 1_000_000)

    def test_a_directory_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "a-directory"
        target.mkdir()
        with pytest.raises(ConfigError, match="not a regular file"):
            read_artifact(target, 1_000_000)

    def test_a_symlink_escaping_the_reviewed_repo_is_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "outside" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        planted = repo / "docs" / "design.md"
        planted.symlink_to(secret)

        with pytest.raises(ConfigError, match="outside it"):
            read_artifact(planted, 1_000_000, containment_root=repo)

    def test_a_symlink_inside_the_reviewed_repo_is_allowed(self, tmp_path: Path) -> None:
        """Refusing every symlink would break ordinary checkouts to no benefit."""
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        real = repo / "real.md"
        real.write_text("# Design\n", encoding="utf-8")
        link = repo / "docs" / "design.md"
        link.symlink_to(real)

        assert read_artifact(link, 1_000_000, containment_root=repo) == "# Design\n"

    def test_an_artifact_outside_the_repo_is_allowed_when_the_operator_says_so(
        self, tmp_path: Path
    ) -> None:
        """Containment applies to where the operator POINTED, not to every path.

        Naming a document that lives elsewhere while passing `--artifact-repo` so the
        repository's own decision log is what DecisionGate reads is a legitimate pattern;
        the attack this guards is a symlink *inside* the reviewed tree escaping it.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        elsewhere = tmp_path / "design.md"
        elsewhere.write_text("# Elsewhere\n", encoding="utf-8")

        assert read_artifact(elsewhere, 1_000_000, containment_root=repo) == "# Elsewhere\n"

    def test_containment_is_not_applied_when_no_repo_is_given(self, tmp_path: Path) -> None:
        target = tmp_path / "design.md"
        target.write_text("# Plain\n", encoding="utf-8")
        assert read_artifact(target, 1_000_000) == "# Plain\n"

    def test_the_size_cap_still_applies_to_a_regular_file(self, tmp_path: Path) -> None:
        target = tmp_path / "design.md"
        target.write_text("x" * 500, encoding="utf-8")
        with pytest.raises(ConfigError, match="exceeds"):
            read_artifact(target, 100)


class TestArtifactPathEdgeCases:
    """G18: the containment check's error branch is attacker-reachable.

    `except (OSError, RuntimeError, ValueError)` around `path.resolve()` looks defensive
    until you notice that the reviewed worktree is untrusted and git carries symlinks: a
    symlink loop inside it makes `resolve()` raise `OSError(ELOOP)`, so this is a path a
    reviewed repository can choose, not a theoretical one.
    """

    def test_a_symlink_loop_at_the_artifact_is_a_typed_config_error(self, tmp_path: Path) -> None:
        """The loop fails at `stat()` with ELOOP, before resolution is even attempted."""
        repo = tmp_path / "worktree"
        (repo / "docs").mkdir(parents=True)
        first = repo / "docs" / "design.md"
        second = repo / "docs" / "other.md"
        first.symlink_to(second)
        second.symlink_to(first)
        with pytest.raises(ConfigError, match="cannot read artifact"):
            read_artifact(first, 10_000, containment_root=repo)

    @pytest.mark.parametrize("raised", [OSError("ELOOP"), RuntimeError("loop"), ValueError("x")])
    def test_an_unresolvable_path_is_a_typed_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception
    ) -> None:
        """The `except (OSError, RuntimeError, ValueError)` branch, driven directly.

        This test used to plant a symlink loop and rely on `Path.resolve()` raising
        `RuntimeError`. It does on 3.11 and **not on 3.13**, where `resolve()` returns the
        path unchanged — so the test passed on two of the three supported interpreters and
        failed CI on the third with "DID NOT RAISE". It was asserting CPython's behaviour
        rather than this function's promise.

        The promise is that whatever the standard library does when a path cannot be
        resolved, the caller gets a typed `ConfigError` and not an untyped crash. All three
        exception types are parametrized because all three are caught, and a tuple member
        nothing exercises is a tuple member nobody knows is needed.
        """
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        artifact = repo / "docs" / "design.md"
        artifact.write_text("# Design\n", encoding="utf-8")

        def explode(self: Path, *args: object, **kwargs: object) -> Path:
            raise raised

        monkeypatch.setattr(Path, "resolve", explode)
        with pytest.raises(ConfigError, match="cannot resolve artifact"):
            read_artifact(artifact, 10_000, containment_root=repo)

    def test_a_symlink_loop_does_not_crash_whatever_pathlib_does(self, tmp_path: Path) -> None:
        """The real-world shape, asserted without depending on which call raises.

        A reviewed worktree can contain a symlink loop, and across supported interpreters
        it surfaces differently — `stat()` raises ELOOP on every version, `resolve()` only
        on some. Either way the caller must get a typed error naming the artifact, never an
        untyped traceback.
        """
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        first = repo / "docs" / "design.md"
        second = repo / "docs" / "other.md"
        first.symlink_to(second)
        second.symlink_to(first)
        with pytest.raises(ConfigError):
            read_artifact(first, 10_000, containment_root=repo)

    def test_a_character_device_is_refused_before_it_is_read(self, tmp_path: Path) -> None:
        """`stat().st_size` reports 0 for /dev/zero, so the size cap passed and the read
        that followed was unbounded — a plausible out-of-memory from a one-line symlink."""
        link = tmp_path / "design.md"
        link.symlink_to("/dev/zero")
        with pytest.raises(ConfigError, match="not a regular file"):
            read_artifact(link, 1_000_000)

    def test_a_directory_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "a-directory"
        target.mkdir()
        with pytest.raises(ConfigError, match="not a regular file"):
            read_artifact(target, 1_000_000)

    def test_a_symlink_escaping_the_reviewed_repo_is_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "outside" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        planted = repo / "docs" / "design.md"
        planted.symlink_to(secret)

        with pytest.raises(ConfigError, match="outside it"):
            read_artifact(planted, 1_000_000, containment_root=repo)

    def test_a_symlink_inside_the_reviewed_repo_is_allowed(self, tmp_path: Path) -> None:
        """Refusing every symlink would break ordinary checkouts to no benefit."""
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        real = repo / "real.md"
        real.write_text("# Design\n", encoding="utf-8")
        link = repo / "docs" / "design.md"
        link.symlink_to(real)

        assert read_artifact(link, 1_000_000, containment_root=repo) == "# Design\n"

    def test_an_artifact_outside_the_repo_is_allowed_when_the_operator_says_so(
        self, tmp_path: Path
    ) -> None:
        """Containment applies to where the operator POINTED, not to every path.

        Naming a document that lives elsewhere while passing `--artifact-repo` so the
        repository's own decision log is what DecisionGate reads is a legitimate pattern;
        the attack this guards is a symlink *inside* the reviewed tree escaping it.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        elsewhere = tmp_path / "design.md"
        elsewhere.write_text("# Elsewhere\n", encoding="utf-8")

        assert read_artifact(elsewhere, 1_000_000, containment_root=repo) == "# Elsewhere\n"

    def test_containment_is_not_applied_when_no_repo_is_given(self, tmp_path: Path) -> None:
        target = tmp_path / "design.md"
        target.write_text("# Plain\n", encoding="utf-8")
        assert read_artifact(target, 1_000_000) == "# Plain\n"

    def test_the_size_cap_still_applies_to_a_regular_file(self, tmp_path: Path) -> None:
        target = tmp_path / "design.md"
        target.write_text("x" * 500, encoding="utf-8")
        with pytest.raises(ConfigError, match="exceeds"):
            read_artifact(target, 100)

    def test_a_file_that_grows_after_the_stat_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The size cap is checked twice because the first check is a claim about the
        filesystem's metadata and the second is a fact about the bytes in hand.

        The window between them is real: the artifact lives in a repository the reviewer
        does not control, and a 20 MB cap that can be stepped over by growing the file
        after `stat` is not a cap. Simulated rather than raced, because a test that has to
        win a race to fail is a test that reports flake.
        """
        artifact = tmp_path / "design.md"
        artifact.write_text("small", encoding="utf-8")
        monkeypatch.setattr(Path, "read_bytes", lambda self: b"x" * 5_000)
        with pytest.raises(ConfigError, match="exceeds"):
            read_artifact(artifact, 100)

    def test_a_read_failure_after_a_successful_stat_is_a_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Permissions can change, and a removable volume can go away, between the two."""
        artifact = tmp_path / "design.md"
        artifact.write_text("content", encoding="utf-8")

        def vanished(self: Path) -> bytes:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(Path, "read_bytes", vanished)
        with pytest.raises(ConfigError, match="cannot read artifact"):
            read_artifact(artifact, 100_000)
