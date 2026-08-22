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
