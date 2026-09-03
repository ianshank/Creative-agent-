"""End-to-end pipeline runs against the fake LLM — one test per spec scenario."""

import asyncio
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from creative_agent.errors import (
    BudgetExceededError,
    ConfigError,
    ExitCode,
    LLMOutputError,
    LLMTimeoutError,
    ReviewFailedError,
    StateConflictError,
)
from creative_agent.harness.llm.base import CallKind, ToolEvidence
from creative_agent.harness.llm.fake import FakeLLMClient, script_key
from creative_agent.models.findings import Severity
from creative_agent.models.oracle import LabelElement, OakConformanceSpec
from creative_agent.models.output import ReviewReport, Verdict
from creative_agent.models.sweeps import ScopeItem
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


class TestDeterministicGatesCannotBeWaived:
    """The model supplies judgement, never permission: a deterministic obligation must
    not be clearable by the model's own say-so."""

    async def test_model_cannot_waive_a_missing_required_section(self, tmp_path: Path) -> None:
        # classify claims the safety section is present; the artifact has no such heading.
        scripts = base_scripts(
            classify=classify_payload(artifact_class="deployment_blueprint", safety=True)
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, PLAIN_ARTIFACT))
        assert any("safety section" in f.summary for f in outcome.result.findings)

    async def test_real_heading_satisfies_the_section_gate(self, tmp_path: Path) -> None:
        scripts = base_scripts(
            classify=classify_payload(artifact_class="deployment_blueprint", safety=False)
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        # CONFORMANT_ARTIFACT carries a real "## Safety" heading.
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert not any("safety section" in f.summary for f in outcome.result.findings)


class TestUnrepairableDefects:
    async def test_unrepairable_defect_fails_without_burning_the_budget(
        self, tmp_path: Path
    ) -> None:
        """Tool-honesty and attribution defects name no row, so re-running the same
        calls cannot fix them; the review must fail immediately rather than pay for
        identical retries."""
        scripts = base_scripts(synthesis_count=0)
        bad = synthesis_payload(headline="Doe would say this is fine.")
        scripts[script_key(CallKind.SYNTHESIS)] = [raw(CallKind.SYNTHESIS, bad)]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake, max_regen=2)
        with pytest.raises(ReviewFailedError, match="no repairable call"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        synthesis_calls = [c for c in fake.calls if c.kind is CallKind.SYNTHESIS]
        assert len(synthesis_calls) == 1, "must not re-synthesize when nothing is repairable"


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
        # Findings with no doctrine row group under the oracle's placeholder key, which
        # deliberately falls outside the row-id grammar so it cannot collide with a row.
        placeholder = oracle.protocol.placeholder_row_id
        assert any(
            f.key.render() == f"{placeholder}+dec-s1-missing-decision"
            for f in outcome.result.findings
        )


class TestPipelineBranches:
    """Paths the happy-path suite never reaches."""

    async def test_explicit_mode_bypasses_auto_detection(self, tmp_path: Path) -> None:
        scripts = base_scripts(classify=classify_payload(recommendation="advisory", evidence=None))
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        request = request_for(tmp_path, PLAIN_ARTIFACT, mode="conformance")
        outcome = await pipeline.run(request)
        assert outcome.result.mode == "conformance"
        assert not outcome.result.mode_uncertain

    async def test_unknown_artifact_class_reprobes_then_fails(self, tmp_path: Path) -> None:
        bad = classify_payload(artifact_class="poem")
        scripts = base_scripts(classify=bad)
        scripts[script_key(CallKind.CLASSIFY)].append(raw(CallKind.CLASSIFY, bad))
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        with pytest.raises(LLMOutputError, match="unknown artifact class"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

    async def test_unknown_artifact_class_recovers_on_reprobe(self, tmp_path: Path) -> None:
        scripts = base_scripts(classify=classify_payload(artifact_class="poem"))
        scripts[script_key(CallKind.CLASSIFY)].append(raw(CallKind.CLASSIFY, classify_payload()))
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.result.artifact_class == "architecture_design"

    async def test_schema_violation_retries_then_raises(self, tmp_path: Path) -> None:
        scripts = base_scripts()
        scripts[script_key(CallKind.CLASSIFY)] = [
            raw(CallKind.CLASSIFY, {"nonsense": True}),
            raw(CallKind.CLASSIFY, {"nonsense": True}),
        ]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake, max_regen=1)
        with pytest.raises(LLMOutputError, match="failed schema validation"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

    async def test_mislabelled_row_response_is_normalized(self, tmp_path: Path) -> None:
        """A model answering for the wrong row must not overwrite another row's verdict."""
        scripts = base_scripts(
            rows={"D2": [row_payload("D1", "not_applicable", na_reason="wrong row id")]}
        )
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        by_row = {d.row_id: d for d in outcome.report.row_dispositions}
        assert set(by_row) == {"D1", "D2"}
        assert by_row["D2"].na_reason == "wrong row id"

    async def test_source_quality_and_judgement_findings_are_assembled(
        self, tmp_path: Path
    ) -> None:
        evidence = [
            ToolEvidence(tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True)
        ]
        scripts = base_scripts(evidence=evidence)
        scripts[script_key(CallKind.SOURCE_QUALITY)] = [
            raw(
                CallKind.SOURCE_QUALITY,
                {
                    "load_bearing_violations": [
                        finding_payload(anchor="preprint-carries-two-conclusions")
                    ],
                    "regime_break_findings": [finding_payload(anchor="supervised-to-rl")],
                    "verification_entries": [verified_entry("D1")],
                },
            )
        ]
        scripts[script_key(CallKind.JUDGEMENT)] = [
            raw(
                CallKind.JUDGEMENT,
                {
                    "baselines": [
                        {
                            "advantage": "faster",
                            "simplest_baseline": "a linear model",
                            "compared_in_artifact": False,
                            "finding": finding_payload(anchor="no-baseline-comparison"),
                        }
                    ],
                    "falsifiability": [
                        {
                            "prediction": "error drops",
                            "surprising_result": "no drop at all",
                            "above_null": False,
                            "finding": finding_payload(anchor="threshold-below-null"),
                        }
                    ],
                    "scope": [
                        {
                            "reference": "companion safety analysis",
                            "supplied": False,
                            "treated_as_unverified": True,
                        }
                    ],
                    "verification_entries": [verified_entry("D1")],
                },
            )
        ]
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        anchors = {f.key.slug for f in outcome.result.findings}
        assert {
            "preprint-carries-two-conclusions",
            "supervised-to-rl",
            "no-baseline-comparison",
            "threshold-below-null",
        } <= anchors
        assert outcome.report.scope_items[0].reference == "companion safety analysis"
        assert "companion safety analysis" in outcome.rendered


class TestRunBudgetAndTimeout:
    """`max_budget_usd` was per call and `llm_timeout_seconds` was dead (DEC-F17).

    A review makes 18 logical calls on the happy path and up to ~144 provider calls once
    the classify re-probe, the repair loop and `_call`'s own schema-retry loop compound, so
    a per-call budget permitted the setting multiplied by that. `cost_usd` was logged and
    stored per call and never summed against anything.
    """

    async def test_a_run_under_budget_completes(self, tmp_path: Path) -> None:
        fake = FakeLLMClient(base_scripts(cost_usd=0.001))
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=10.0)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.report.cycle == 1

    async def test_an_exhausted_budget_aborts_the_run(self, tmp_path: Path) -> None:
        fake = FakeLLMClient(base_scripts(cost_usd=1.0))
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=2.0)
        with pytest.raises(BudgetExceededError) as excinfo:
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert "budget" in str(excinfo.value).lower()

    async def test_an_aborted_run_publishes_nothing(self, tmp_path: Path) -> None:
        """The never-soften rule: a partial sweep must not reach state or the report."""
        fake = FakeLLMClient(base_scripts(cost_usd=1.0))
        pipeline, store = build_pipeline(tmp_path, fake, max_budget_usd=2.0)
        with pytest.raises(BudgetExceededError):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert store.load("design").cycle == 0
        assert not store.path_for("design").exists()

    async def test_no_budget_configured_means_no_limit(self, tmp_path: Path) -> None:
        fake = FakeLLMClient(base_scripts(cost_usd=1_000.0))
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=None)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.report.cycle == 1

    async def test_a_backend_reporting_no_cost_does_not_abort(self, tmp_path: Path) -> None:
        """OfflineLLMClient and FakeLLMClient both leave cost_usd None by design."""
        fake = FakeLLMClient(base_scripts())
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=0.01)
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.report.cycle == 1

    async def test_a_hanging_call_times_out_instead_of_blocking_forever(
        self, tmp_path: Path
    ) -> None:
        class Hanging:
            async def generate(self, prompt: object) -> object:
                await asyncio.sleep(3600)
                raise AssertionError("unreachable")

        pipeline, store = build_pipeline(
            tmp_path, FakeLLMClient(base_scripts()), llm_timeout_seconds=0.01
        )
        pipeline._llm = Hanging()  # type: ignore[assignment]
        with pytest.raises(LLMTimeoutError):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert not store.path_for("design").exists()

    async def test_budget_and_timeout_aborts_share_the_retryable_exit_code(self) -> None:
        """A CI consumer must be able to tell "retry" from "the document is wrong"."""
        assert BudgetExceededError.exit_code is ExitCode.RUN_ABORTED
        assert LLMTimeoutError.exit_code is ExitCode.RUN_ABORTED
        assert ReviewFailedError.exit_code is not ExitCode.RUN_ABORTED


class TestBudgetOvershootIsBounded:
    """A pre-call check cannot know what the call will cost (adversarial review, M4).

    With `max_budget_usd=2.0` and $1.90 calls the run spent $3.80 before stopping — 190% of
    the setting — because the check was `spent < budget` with no reservation for the call
    about to be made. Separately, the adapter passed the same setting to the SDK as a *per
    call* cap, so one number meant two different things and the practical bound was roughly
    twice the setting. The remaining run budget is now the per-call ceiling.
    """

    async def test_the_remaining_budget_reaches_the_backend_as_its_per_call_cap(
        self, tmp_path: Path
    ) -> None:
        seen: list[float | None] = []

        class Recording:
            def __init__(self, inner: FakeLLMClient) -> None:
                self._inner = inner

            async def generate(self, prompt: Any) -> Any:
                seen.append(prompt.remaining_budget_usd)
                return await self._inner.generate(prompt)

        fake = FakeLLMClient(base_scripts(cost_usd=0.25))
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=10.0)
        pipeline._llm = Recording(fake)  # type: ignore[assignment]
        await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

        assert seen[0] == 10.0, "the first call should see the whole budget"
        assert seen[-1] < seen[0], "the remainder must shrink as the run spends"
        assert all(value is not None and value >= 0.0 for value in seen)

    async def test_an_uncapped_run_passes_no_per_call_ceiling(self, tmp_path: Path) -> None:
        seen: list[float | None] = []

        class Recording:
            def __init__(self, inner: FakeLLMClient) -> None:
                self._inner = inner

            async def generate(self, prompt: Any) -> Any:
                seen.append(prompt.remaining_budget_usd)
                return await self._inner.generate(prompt)

        fake = FakeLLMClient(base_scripts())
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=None)
        pipeline._llm = Recording(fake)  # type: ignore[assignment]
        await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

        assert seen and all(value is None for value in seen)

    async def test_the_budget_check_runs_inside_the_retry_loop(self, tmp_path: Path) -> None:
        """DEC-F17's placement claim, which no test previously distinguished.

        Every scripted payload validated first time, so there was one attempt per call and
        moving `_check_budget` above the attempt loop left the suite green. A row call that
        fails schema validation once forces a second attempt, and the budget consumed by
        the first attempt must be seen by the second.
        """
        seen: list[float | None] = []

        class Recording:
            def __init__(self, inner: FakeLLMClient) -> None:
                self._inner = inner

            async def generate(self, prompt: Any) -> Any:
                seen.append(prompt.remaining_budget_usd)
                return await self._inner.generate(prompt)

        scripts = base_scripts(cost_usd=0.5, rows={"D1": [{"row_id": "D1"}, row_payload("D1")]})
        fake = FakeLLMClient(scripts)
        pipeline, _ = build_pipeline(tmp_path, fake, max_budget_usd=100.0, max_regen=1)
        pipeline._llm = Recording(fake)  # type: ignore[assignment]
        await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

        # Strictly decreasing across every attempt, retries included: a check hoisted out
        # of the attempt loop would repeat a value across the two attempts of one call.
        assert seen == sorted(seen, reverse=True)
        assert len(set(seen)) == len(seen), "a repeated remainder means an attempt was not counted"


class TestTheWiringIsHeldTooNotJustTheLeaves:
    """Five one-line deletions in `pipeline.py` each left the whole suite green.

    A gap analysis of this branch found the same shape five times: the leaf behaviour is
    well tested at unit level, and the line that switches it on in the composed pipeline
    is not. `FileStateStore.save`'s conflict check is unit-tested six ways, and the
    pipeline passing `expected_cycle` is tested by nothing — and `expected_cycle`
    defaults to None, which means "skip the check". A control that is opt-in and whose
    opt-in is unheld is a control that is off.

    These are integration assertions on the composed pipeline, deliberately, because that
    is the level at which the defect lives.
    """

    async def test_a_concurrent_write_aborts_the_run_instead_of_erasing_it(
        self, tmp_path: Path
    ) -> None:
        """G1/DEC-F14: the lost-update guard is only armed if the caller arms it.

        Simulates the DEC-F14 scenario exactly: this run loads cycle N, another review of
        the same artifact finishes and writes N+1, and this run then tries to publish. The
        losing run must abort rather than `os.replace` the other's history away — that
        history carries the recurrence counts driving the cycle-3 charter-review STOP.
        """
        pipeline, store = build_pipeline(tmp_path, FakeLLMClient(base_scripts()))
        real_load = store.load

        def load_then_let_the_other_review_finish(artifact_id: str) -> Any:
            state = real_load(artifact_id)
            store.load = real_load  # type: ignore[method-assign]
            store.save(state.model_copy(update={"cycle": state.cycle + 1}), "other run")
            return state

        store.load = load_then_let_the_other_review_finish  # type: ignore[method-assign]
        with pytest.raises(StateConflictError):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert store.load("design").cycle == 1, "the other run's write must survive"

    async def test_an_artifact_symlinked_out_of_the_reviewed_repo_is_refused(
        self, tmp_path: Path
    ) -> None:
        """G6: `read_artifact`'s containment check is well tested; its wiring was not.

        The one integration test that passes `artifact_repo` uses an artifact outside the
        repo, so containment never applied and deleting `containment_root=` from the
        pipeline changed nothing observable.
        """
        repo = tmp_path / "worktree"
        (repo / "docs").mkdir(parents=True)
        secret = tmp_path / "outside" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        artifact = repo / "docs" / "design.md"
        artifact.symlink_to(secret)

        pipeline, store = build_pipeline(tmp_path, FakeLLMClient(base_scripts()))
        request = request_for(tmp_path, CONFORMANT_ARTIFACT).model_copy(
            update={"artifact_path": artifact, "artifact_repo": repo}
        )
        with pytest.raises(ConfigError, match="outside it"):
            await pipeline.run(request)
        assert store.load("design").cycle == 0, "nothing may be published from a refused read"

    async def test_a_finding_citing_an_unknown_row_keeps_its_sweep_attribution(
        self, tmp_path: Path
    ) -> None:
        """G10: the default-row fallback for a row finding whose refs are unusable.

        With the fallback off, `doctrine_refs` is empty, `check_completeness` iterates
        nothing and therefore demands no verification entry at all, and an unbacked
        finding publishes. It also re-keys under the placeholder row id, which breaks
        recurrence tracking for escalation.
        """
        scripts = base_scripts(
            rows={
                "D1": [
                    row_payload(
                        "D1",
                        "miss",
                        findings=[finding_payload(severity="minor", row_id="D99")],
                        entries=[verified_entry("D1")],
                    )
                ]
            },
            evidence=[
                ToolEvidence(
                    tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True
                )
            ],
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        (finding,) = outcome.result.findings
        assert finding.doctrine_refs == ["D1"]
        assert finding.key.row_id == "D1"

    async def test_the_report_is_not_rendered_on_the_failure_path(self, tmp_path: Path) -> None:
        """P11: the report was fully assembled and rendered, then discarded by a raise.

        Not just wasted work — computing it invites the next reader to assume the rendered
        text is available to the error handler, and it is not.
        """
        rendered_calls: list[object] = []
        scripts = base_scripts(
            rows={
                "D1": [
                    row_payload("D1", "miss", findings=[finding_payload()]),
                    row_payload("D1", "miss", findings=[finding_payload()]),
                ]
            }
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts), max_regen=0)
        real_render = pipeline._renderer.render

        def spy(report: Any) -> str:
            rendered_calls.append(report)
            return real_render(report)

        pipeline._renderer.render = spy  # type: ignore[method-assign]
        with pytest.raises(ReviewFailedError):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert rendered_calls == []


class TestReportBoundaryLaundering:
    """DEC-F22: laundering is a boundary, not a list of field names.

    DEC-F16 said "every model prose field is laundered" and missed two. DEC-F19 added
    those two and missed two more — and the two it missed were `VerificationEntry.
    assertion` and `RowDisposition.na_reason`, which are the verification log and the
    doctrine sweep: the two tables a reviewer actually reads. Three rounds of "add the
    field we forgot" is the evidence that enumerating fields to include is the defect.

    So these tests assert a property of the whole report rather than of named fields: no
    Unicode format character survives anywhere, whatever the model put where.
    """

    BIDI = "\u202e"
    ZERO_WIDTH = "\u200b"

    @staticmethod
    def _format_chars(text: str) -> list[str]:
        return [c for c in text if unicodedata.category(c) == "Cf"]

    async def test_a_bidi_override_in_a_doctrine_verdict_reason_never_reaches_the_report(
        self, tmp_path: Path
    ) -> None:
        """`na_reason` renders the doctrine sweep's N/A column.

        A bidi override there displays a verdict as the reverse of what review state
        records, so the published report and the audit trail disagree while both look
        correct to the person comparing them.
        """
        poisoned = f"no such{self.BIDI} claims{self.ZERO_WIDTH} here"
        scripts = base_scripts(
            rows={"D2": [row_payload("D2", "not_applicable", na_reason=poisoned)]}
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert self._format_chars(outcome.rendered) == []
        assert all(
            self._format_chars(d.na_reason or "") == [] for d in outcome.report.row_dispositions
        )

    async def test_a_newline_in_a_verification_assertion_cannot_forge_a_row(
        self, tmp_path: Path
    ) -> None:
        """`assertion` is the first cell of the verification-log table.

        A newline there ends the row; what follows is a table row of the model's choosing
        in the log that DEC-F9 calls the control worth reading.
        """
        entry = verified_entry("D1")
        entry["assertion"] = "checked\n| forged | D2 | x | Certain | fetched | verified |"
        scripts = base_scripts(
            rows={"D1": [row_payload("D1", "hit", entries=[entry])]},
            evidence=[
                ToolEvidence(
                    tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True
                )
            ],
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert all("\n" not in e.assertion for e in outcome.report.verification_log)
        assert "forged" not in outcome.rendered.split("## Verification log")[-1].split("\n|")[0]

    async def test_a_scope_reference_is_laundered_by_the_boundary(self, tmp_path: Path) -> None:
        """G7: the previous per-field fix for `scope_items` was itself unheld — the test
        asserted a reference string that laundering does not alter, so both the fixed and
        the unfixed implementation satisfied it."""
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(base_scripts()))
        report = pipeline._guard.launder_model(
            ReviewReport(
                artifact_id="a",
                oracle_id="o",
                oracle_version="1",
                cycle=1,
                verdict=Verdict(mode="advisory", confidence="Likely", headline="h"),
                scope_items=[
                    ScopeItem(
                        reference=f"companion{self.BIDI}\n\n## Findings\n\nNone.",
                        supplied=False,
                        treated_as_unverified=True,
                    )
                ],
            )
        )
        assert "\n" not in report.scope_items[0].reference
        assert self._format_chars(report.scope_items[0].reference) == []

    async def test_structural_identifiers_survive_laundering_byte_for_byte(
        self, tmp_path: Path
    ) -> None:
        """The exclusion list is the other half of the contract.

        Laundering a `finding_id` or a recurrence `slug` would silently break cycle
        escalation across runs, which is a worse failure than the one this fixes because
        it is invisible in a single review.
        """
        scripts = base_scripts(
            rows={
                "D1": [
                    row_payload(
                        "D1",
                        "miss",
                        findings=[finding_payload(severity="minor")],
                        entries=[verified_entry("D1")],
                    )
                ]
            },
            evidence=[
                ToolEvidence(
                    tool_name="WebFetch", target="https://arxiv.org/abs/2001.00001", ok=True
                )
            ],
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        (finding,) = outcome.result.findings
        assert finding.finding_id in outcome.rendered
        assert finding.key.slug == "some-defect"


class TestAModelChosenEnumIsValidatedEverywhereItEnters:
    """DEC-F24: the mode re-probe assigned an unvalidated `artifact_class`.

    The first classify probe validates against the oracle's known classes, re-probes once
    and raises a typed `LLMOutputError`. The mode re-probe assigned its result straight
    through, and the next line looked the class up with a bare `next()`. A model that
    answers validly once and invalidly on the re-probe therefore raised StopIteration
    inside a coroutine — RuntimeError, which is not a CreativeAgentError, so the CLI's
    typed handler misses it and a malformed model response is reported as exit 5
    "unexpected error" rather than exit 3.
    """

    async def test_an_unknown_class_on_the_mode_reprobe_is_a_typed_output_error(
        self, tmp_path: Path
    ) -> None:
        conformance_markers_but_no_quote = classify_payload(
            recommendation="advisory", evidence=None
        )
        scripts = base_scripts(classify=conformance_markers_but_no_quote)
        scripts[script_key(CallKind.CLASSIFY)].extend(
            [
                raw(CallKind.CLASSIFY, classify_payload(artifact_class="totally_unknown_class")),
                raw(CallKind.CLASSIFY, classify_payload(artifact_class="still_unknown")),
            ]
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        with pytest.raises(LLMOutputError, match="unknown artifact class"):
            await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))

    async def test_the_reprobe_recovers_when_the_second_answer_is_valid(
        self, tmp_path: Path
    ) -> None:
        """The re-probe must still be a re-probe: one bad answer is not a failed review."""
        scripts = base_scripts(classify=classify_payload(recommendation="advisory", evidence=None))
        scripts[script_key(CallKind.CLASSIFY)].extend(
            [
                raw(CallKind.CLASSIFY, classify_payload(artifact_class="unknown_first")),
                raw(CallKind.CLASSIFY, classify_payload()),
            ]
        )
        pipeline, _ = build_pipeline(tmp_path, FakeLLMClient(scripts))
        outcome = await pipeline.run(request_for(tmp_path, CONFORMANT_ARTIFACT))
        assert outcome.report.cycle == 1
