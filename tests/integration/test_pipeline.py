"""End-to-end pipeline runs against the fake LLM — one test per spec scenario."""

import json
from pathlib import Path

import pytest

from creative_agent.errors import ReviewFailedError
from creative_agent.harness.llm.base import CallKind, ToolEvidence
from creative_agent.harness.llm.fake import FakeLLMClient, script_key
from creative_agent.models.findings import Severity
from creative_agent.models.oracle import LabelElement, OakConformanceSpec
from tests.factories import make_oracle
from tests.integration.pipeline_fixtures import (
    CONFORMANT_ARTIFACT,
    PLAIN_ARTIFACT,
    base_scripts,
    build_pipeline,
    classify_payload,
    finding_payload,
    raw,
    request_for,
    row_payload,
    synthesis_payload,
    verified_entry,
)
from tests.unit.test_state import record, state_with_history


class TestCleanConformanceRun:
    async def test_clean_run(self, tmp_path: Path) -> None:
        fake = FakeLLMClient(base_scripts())
        pipeline, store = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

        assert outcome.result.mode == "conformance"
        assert outcome.result.findings == []
        assert outcome.report.cycle == 1
        assert "VERDICT" in outcome.rendered and "mode=conformance" in outcome.rendered
        fake.assert_consumed()

        # State advanced and audit bundle written.
        state = store.load("design")
        assert state.cycle == 1
        bundle = tmp_path / "review-log" / "design" / "cycle-1"
        calls = json.loads((bundle / "calls.json").read_text(encoding="utf-8"))
        assert {c["kind"] for c in calls} >= {"classify", "row", "synthesis"}

    async def test_row_dispositions_rendered(self, tmp_path: Path) -> None:
        scripts = base_scripts(
            rows={"D2": [row_payload("D2", "not_applicable", na_reason="no such claims")]}
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert "not_applicable" in outcome.rendered
        assert "no such claims" in outcome.rendered


class TestAdvisoryForcing:
    async def test_no_claim_caps_blocker_to_info(self, tmp_path: Path) -> None:
        """Fake returns a Blocker; artifact never claims conformance => Info-capped."""
        evidence = [
            ToolEvidence(tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True)
        ]
        scripts = base_scripts(
            classify=classify_payload(recommendation="advisory", evidence=None, safety=False),
            rows={
                "D1": [
                    row_payload(
                        "D1",
                        "miss",
                        findings=[finding_payload(severity="blocker")],
                        entries=[verified_entry("D1")],
                    )
                ]
            },
            evidence=evidence,
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, PLAIN_ARTIFACT))

        assert outcome.result.mode == "advisory"
        assert not outcome.result.mode_uncertain  # no markers in a plain artifact
        finding = outcome.result.findings[0]
        assert finding.severity is Severity.INFO
        assert finding.original_severity is Severity.BLOCKER
        assert "advisory" in (finding.cap_reason or "")

    async def test_false_advisory_reprobe(self, tmp_path: Path) -> None:
        """Markers present but no evidence quote => one re-probe; still none => uncertain."""
        scripts = base_scripts(
            classify=classify_payload(recommendation="conformance", evidence=None)
        )
        scripts[script_key(CallKind.CLASSIFY)].append(
            raw(
                CallKind.CLASSIFY,
                classify_payload(recommendation="conformance", evidence="not in artifact"),
            )
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.result.mode == "advisory"
        assert outcome.result.mode_uncertain
        assert "mode uncertain" in outcome.rendered

    async def test_reprobe_recovers_conformance(self, tmp_path: Path) -> None:
        scripts = base_scripts(
            classify=classify_payload(recommendation="conformance", evidence=None)
        )
        scripts[script_key(CallKind.CLASSIFY)].append(raw(CallKind.CLASSIFY, classify_payload()))
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.result.mode == "conformance"
        assert not outcome.result.mode_uncertain


class TestVerificationEnforcement:
    async def test_unbacked_finding_repairs_then_fails(self, tmp_path: Path) -> None:
        """A doctrinal finding with no verification entry: one repair, then ReviewFailed."""
        defective = row_payload("D1", "miss", findings=[finding_payload()])
        scripts = base_scripts(
            rows={"D1": [defective, defective]},  # initial + repair, both defective
            synthesis_count=2,
        )
        fake = FakeLLMClient(scripts)
        pipeline, store = build_pipeline(tmp_path, fake, max_regen=1)
        with pytest.raises(ReviewFailedError, match="verification log"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        fake.assert_consumed()
        assert store.load("design").cycle == 0  # failed reviews never advance state

    async def test_repair_recovers(self, tmp_path: Path) -> None:
        evidence = [
            ToolEvidence(tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True)
        ]
        defective = row_payload("D1", "miss", findings=[finding_payload()])
        repaired = row_payload(
            "D1", "miss", findings=[finding_payload()], entries=[verified_entry("D1")]
        )
        scripts = base_scripts(
            rows={"D1": [defective, repaired]}, synthesis_count=2, evidence=evidence
        )
        fake = FakeLLMClient(scripts)
        pipeline, store = build_pipeline(tmp_path, fake, max_regen=1)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert len(outcome.result.findings) == 1
        assert store.load("design").cycle == 1
        fake.assert_consumed()

    async def test_impersonation_in_synthesis_fails_review(self, tmp_path: Path) -> None:
        scripts = base_scripts(synthesis_count=0)
        bad = synthesis_payload(headline="Doe would say this is fine.")
        scripts[script_key(CallKind.SYNTHESIS)] = [
            raw(CallKind.SYNTHESIS, bad),
            raw(CallKind.SYNTHESIS, bad),
        ]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake, max_regen=1)
        with pytest.raises(ReviewFailedError, match="impersonation"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))


class TestEscalation:
    async def test_three_cycle_recurring_major_stops(self, tmp_path: Path) -> None:
        evidence = [
            ToolEvidence(tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True)
        ]
        scripts = base_scripts(
            rows={
                "D1": [
                    row_payload(
                        "D1",
                        "miss",
                        findings=[finding_payload(anchor="some defect")],
                        entries=[verified_entry("D1")],
                    )
                ]
            },
            evidence=evidence,
        )
        fake = FakeLLMClient(scripts)
        pipeline, store = build_pipeline(tmp_path, fake)
        # Seed two prior cycles with the same open Major key (D1 + "some-defect").
        prior = state_with_history(
            [record(1, [("D1", "some-defect")]), record(2, [("D1", "some-defect")])]
        )
        prior = prior.model_copy(update={"artifact_id": "design"})
        store.save(prior)

        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.result.escalation is not None
        assert outcome.result.escalation.cycles == [1, 2, 3]
        assert "STOP — charter review triggered" in outcome.rendered


class TestSpecDeltaScenarios:
    async def test_oak_label_refusal_names_missing_elements(self, tmp_path: Path) -> None:
        oracle = make_oracle(
            oak_conformance=OakConformanceSpec(
                label_pattern=r"\bOaK\b",
                doctrine_ref="D1",
                missing_severity=Severity.MAJOR,
                features=[
                    LabelElement(label="Feature 1: continual", patterns=["learn continually"]),
                    LabelElement(
                        label="Feature 2: per-weight step sizes",
                        patterns=["per-weight step size"],
                    ),
                ],
                stages=[
                    LabelElement(
                        label="Stage 1: Feature Construction", patterns=["feature construction"]
                    ),
                ],
            )
        )
        artifact = (
            "# OaK-style agent\n\nWe follow OaK: all parts learn continually via "
            "feature construction.\n"
        )
        fake = FakeLLMClient(
            base_scripts(
                classify=classify_payload(recommendation="advisory", evidence=None, safety=False)
            )
        )
        pipeline, _ = build_pipeline(tmp_path, fake, oracle=oracle)
        outcome = await pipeline.run(request_for(tmp_path, artifact))
        summaries = [f.summary for f in outcome.result.findings]
        assert any("feature 2" in s.lower() for s in summaries)
        assert not any("feature 1" in s.lower() for s in summaries)
        assert not any("stage 1" in s.lower() for s in summaries)

    async def test_blueprint_compute_budget_blocker(self, tmp_path: Path) -> None:
        scripts = base_scripts(classify=classify_payload(artifact_class="deployment_blueprint"))
        scripts[script_key(CallKind.CLAIMS)] = [
            raw(
                CallKind.CLAIMS,
                {
                    "claims": [
                        {
                            "claim": "processes 100Hz control on the named SoC",
                            "section": "hardware",
                            "gate_fields": {
                                "observable": {"stated": True, "text": "rate"},
                                "falsifier": {"stated": True, "text": "threshold"},
                            },
                            "provenance": {"dataset": True, "baseline": True},
                        }
                    ]
                },
            )
        ]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        blockers = [f for f in outcome.result.findings if f.original_severity is Severity.BLOCKER]
        assert blockers and "compute_budget" in blockers[0].summary
        # conformance mode: blocker survives capping (gate_failure basis).
        assert blockers[0].severity is Severity.BLOCKER

    async def test_internal_contradiction_detected_deterministically(self, tmp_path: Path) -> None:
        artifact = CONFORMANT_ARTIFACT + "\ngamma = 0.99\n\ngamma = 0.95\n"
        fake = FakeLLMClient(base_scripts())
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, artifact))
        contradiction = [
            f
            for f in outcome.result.findings
            if any(s.kind == "internal_contradiction" for s in f.supports)
        ]
        assert contradiction and contradiction[0].severity is Severity.BLOCKER

    async def test_research_synthesis_skips_rows_and_gates(self, tmp_path: Path) -> None:
        scripts = base_scripts(classify=classify_payload(artifact_class="research_synthesis"))
        # No ROW/CLAIMS/JUDGEMENT calls for synthesis-only classes.
        for key in list(scripts):
            if key.startswith(("row:", "claims:", "judgement:")):
                del scripts[key]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.report.row_dispositions == []
        fake.assert_consumed()

    async def test_missing_decision_flagged_from_artifact_repo(self, tmp_path: Path) -> None:
        oracle = make_oracle(required_decisions=["DEC-S1"])
        repo = tmp_path / "target"
        repo.mkdir()
        fake = FakeLLMClient(base_scripts())
        pipeline, _ = build_pipeline(tmp_path, fake, oracle=oracle)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT, artifact_repo=repo))
        assert any(f.key.render() == "G0+dec-s1-missing-decision" for f in outcome.result.findings)
