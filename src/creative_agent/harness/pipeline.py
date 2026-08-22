"""ReviewPipeline: the multi-call orchestration with deterministic assembly.

The LLM answers typed calls (classify, per-row sweeps, claims, source-quality,
judgement, synthesis); every structural rule — severity caps, gate scoring, verification
completeness, tool honesty, escalation, rendering — is enforced here, deterministically.
The findings table is assembled by this module, never emitted whole by the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from creative_agent.config import HarnessSettings
from creative_agent.errors import LLMOutputError, ReviewFailedError
from creative_agent.harness.artifact import content_sha256, read_artifact, resolve_artifact_id
from creative_agent.harness.consistency import ConsistencyChecker
from creative_agent.harness.decisions import DecisionGate
from creative_agent.harness.gates import MeasurementGateChecker
from creative_agent.harness.llm.base import (
    CallKind,
    RawLLMResult,
    ToolEvidence,
    parse_payload,
)
from creative_agent.harness.oak import LabelConformanceChecker
from creative_agent.harness.prompting import PromptAssembler, render_oracle_rows
from creative_agent.harness.protocols import Clock, LLMClient, ReviewAgent, StateStore
from creative_agent.harness.rendering import OutputRenderer
from creative_agent.harness.security import ThreatGuard
from creative_agent.harness.severity import SeverityPolicy
from creative_agent.harness.sourcequality import SourceQualityChecker
from creative_agent.harness.state import CycleEscalator
from creative_agent.harness.verification import VerificationLogChecker
from creative_agent.models.findings import Finding, FindingKey, Severity, normalize_slug
from creative_agent.models.oracle import OracleTable
from creative_agent.models.output import ReviewReport, Verdict
from creative_agent.models.review import ReviewMode, ReviewRequest, ReviewResult
from creative_agent.models.state import CycleRecord, HistoricalFinding
from creative_agent.models.sweeps import (
    CandidateFinding,
    ClaimsResult,
    ClassifyResult,
    JudgementSweepResult,
    RowDisposition,
    SourceQualityResult,
    SynthesisResult,
)
from creative_agent.models.verification import VerificationEntry

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+)$", re.MULTILINE)
_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass
class _SweepState:
    """Mutable working set across the verification/repair loop."""

    rows: dict[str, RowDisposition] = field(default_factory=dict)
    synthesis: SynthesisResult | None = None
    source_quality: SourceQualityResult | None = None
    judgement: JudgementSweepResult | None = None
    evidence: list[ToolEvidence] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)


@dataclass
class PipelineOutcome:
    """Everything a caller (CLI, subagent) needs from one run."""

    report: ReviewReport
    result: ReviewResult
    rendered: str
    state_path: str
    review_failed_reason: str | None = None


class ReviewPipeline:
    def __init__(
        self,
        *,
        agent: ReviewAgent,
        oracle: OracleTable,
        llm: LLMClient,
        settings: HarnessSettings,
        state_store: StateStore,
        clock: Clock,
    ) -> None:
        self._agent = agent
        self._oracle = oracle
        self._llm = llm
        self._settings = settings
        self._state_store = state_store
        self._clock = clock
        self._severity_policy = SeverityPolicy(oracle)
        self._gate_checker = MeasurementGateChecker(oracle)
        self._sq_checker = SourceQualityChecker(oracle.source_quality)
        self._consistency = ConsistencyChecker()
        self._verifier = VerificationLogChecker(oracle.source_author_names())
        self._decision_gate = DecisionGate(oracle, settings.decision_log_filename)
        self._escalator = CycleEscalator(oracle)
        self._guard = ThreatGuard(oracle, settings.max_prose_chars)
        self._assembler = PromptAssembler(settings.prompt_search_paths, agent.prompt_template_dir())
        self._renderer = OutputRenderer(oracle.protocol.unverified_marker)

    # ---- LLM call helper -------------------------------------------------

    async def _call(
        self,
        sweep: _SweepState,
        kind: CallKind,
        model_type: type[_ModelT],
        context: dict[str, object],
        *,
        ref: str = "",
        repair_defects: list[str] | None = None,
    ) -> _ModelT:
        """One typed call with bounded schema-repair retries."""
        attempts = self._settings.max_regeneration_attempts + 1
        defects = list(repair_defects or [])
        last_error: str | None = None
        for _ in range(attempts):
            prompt = self._assembler.assemble(
                kind,
                ref=ref,
                allowed_tools=self._settings.agent_tools,
                fetch_domain_allowlist=context.get("fetch_domains", []),  # type: ignore[arg-type]
                context={**context, "repair_defects": defects},
            )
            raw: RawLLMResult = await self._llm.generate(prompt)
            sweep.evidence.extend(raw.tool_evidence)
            sweep.calls.append(
                {"kind": kind.value, "ref": ref, "model": raw.model, "cost_usd": raw.cost_usd}
            )
            try:
                return parse_payload(raw, model_type)
            except ValidationError as exc:
                last_error = str(exc)
                defects = [*defects, f"schema violation: {exc.errors()[0]['msg']}"]
        raise LLMOutputError(
            f"{kind.value} call failed schema validation after {attempts} attempts: {last_error}"
        )

    # ---- deterministic helpers ------------------------------------------

    def _sections_present(self, text: str, classify: ClassifyResult) -> set[str]:
        headings = {m.group("title").strip().casefold() for m in _HEADING.finditer(text)}
        present: set[str] = set()
        for rule in self._oracle.artifact_classes:
            for section in rule.requires_sections:
                if any(section.casefold() in heading for heading in headings):
                    present.add(section)
        if classify.safety_section_present:
            present.add("safety")
        return present

    def _resolve_mode(
        self, request: ReviewRequest, classify: ClassifyResult, artifact_text: str
    ) -> tuple[ReviewMode, bool]:
        """Fail-closed mode rule (DEC-F6) + marker-based uncertainty flag."""
        if request.mode != "auto":
            return request.mode, False
        folded = artifact_text.casefold()
        markers_present = any(
            marker.casefold() in folded for marker in self._oracle.conformance.markers
        )
        evidence = (classify.conformance_evidence or "").strip()
        evidence_found = bool(evidence) and _squash(evidence) in _squash(artifact_text)
        if classify.mode_recommendation == "conformance" and evidence_found:
            return "conformance", False
        return "advisory", markers_present

    def _candidate_to_finding(
        self,
        candidate: CandidateFinding,
        index: int,
        default_row: str | None = None,
        origin: str = "llm",
    ) -> Finding:
        known_rows = {row.id for row in self._oracle.rows}
        doctrine_refs = [r for r in candidate.doctrine_refs if r in known_rows]
        if not doctrine_refs and default_row:
            doctrine_refs = [default_row]
        supports = [
            s for s in candidate.supports if s.kind != "doctrine_row" or s.ref in known_rows
        ]
        key_row = doctrine_refs[0] if doctrine_refs else "G0"
        return Finding(
            finding_id=f"F{index}",
            origin=origin,
            severity=candidate.severity,
            original_severity=candidate.severity,
            summary=self._guard.launder_prose(candidate.summary),
            doctrine_refs=doctrine_refs,
            gate_refs=candidate.gate_refs,
            supports=supports,
            disposition_required=self._guard.launder_prose(candidate.disposition_required)
            if candidate.disposition_required
            else "",
            key=FindingKey(row_id=key_row, slug=normalize_slug(candidate.anchor) or "finding"),
        )

    def _assemble_findings(
        self,
        sweep: _SweepState,
        deterministic: list[CandidateFinding],
        mode: ReviewMode,
    ) -> list[Finding]:
        candidates: list[tuple[CandidateFinding, str | None, str]] = [
            (c, None, "deterministic") for c in deterministic
        ]
        for row_id, disposition in sorted(sweep.rows.items()):
            candidates.extend((f, row_id, "llm") for f in disposition.findings)
        if sweep.source_quality is not None:
            for candidate in (
                *sweep.source_quality.load_bearing_violations,
                *sweep.source_quality.regime_break_findings,
            ):
                candidates.append((candidate, None, "llm"))
        if sweep.judgement is not None:
            for baseline in sweep.judgement.baselines:
                if baseline.finding is not None:
                    candidates.append((baseline.finding, None, "llm"))
            for falsifiability in sweep.judgement.falsifiability:
                if falsifiability.finding is not None:
                    candidates.append((falsifiability.finding, None, "llm"))
        findings = [
            self._candidate_to_finding(candidate, i + 1, default_row, origin)
            for i, (candidate, default_row, origin) in enumerate(candidates)
        ]
        return self._severity_policy.cap_all(findings, mode)

    def _all_entries(self, sweep: _SweepState) -> list[VerificationEntry]:
        entries: list[VerificationEntry] = []
        for _, disposition in sorted(sweep.rows.items()):
            entries.extend(disposition.verification_entries)
        if sweep.source_quality is not None:
            entries.extend(sweep.source_quality.verification_entries)
        if sweep.judgement is not None:
            entries.extend(sweep.judgement.verification_entries)
        return entries

    def _prose_blocks(self, sweep: _SweepState, findings: list[Finding]) -> list[str]:
        blocks = [f.summary for f in findings]
        if sweep.synthesis is not None:
            blocks.append(sweep.synthesis.headline)
            blocks.extend(sweep.synthesis.what_survives)
            blocks.extend(sweep.synthesis.residual_risks)
        return blocks

    # ---- main entry ------------------------------------------------------

    async def run(self, request: ReviewRequest) -> PipelineOutcome:
        state = self._state_store.load(request.artifact_id)
        artifact_text = read_artifact(request.artifact_path, self._settings.max_artifact_bytes)
        fetch_domains = self._guard.fetch_domain_allowlist(artifact_text)
        delimited = ThreatGuard.delimit_artifact(artifact_text)
        prior_keys = sorted(state.open_major_keys())

        base_context: dict[str, object] = {
            "oracle": self._oracle,
            "artifact": delimited,
            "fetch_domains": fetch_domains,
            "prior_keys": prior_keys,
            **self._agent.build_context(request, self._oracle, state),
        }

        sweep = _SweepState()

        # Step 3: classify + fail-closed mode (with one re-probe).
        classify = await self._call(sweep, CallKind.CLASSIFY, ClassifyResult, base_context)
        known_classes = {c.name for c in self._oracle.artifact_classes}
        if classify.artifact_class not in known_classes:
            classify = await self._call(
                sweep,
                CallKind.CLASSIFY,
                ClassifyResult,
                base_context,
                repair_defects=[
                    f"artifact_class must be one of {sorted(known_classes)}, "
                    f"got {classify.artifact_class!r}"
                ],
            )
            if classify.artifact_class not in known_classes:
                raise LLMOutputError(
                    f"classify returned unknown artifact class {classify.artifact_class!r}"
                )
        mode, mode_uncertain = self._resolve_mode(request, classify, artifact_text)
        if mode == "advisory" and mode_uncertain and request.mode == "auto":
            reprobe = await self._call(
                sweep,
                CallKind.CLASSIFY,
                ClassifyResult,
                base_context,
                repair_defects=[
                    "conformance markers appear in the artifact but no verbatim "
                    "conformance_evidence quote was returned; quote the claiming "
                    "sentence exactly, or confirm the artifact does not claim conformance"
                ],
            )
            mode, mode_uncertain2 = self._resolve_mode(request, reprobe, artifact_text)
            mode_uncertain = mode == "advisory" and mode_uncertain2
            classify = reprobe

        class_context = {**base_context, "mode": mode, "artifact_class": classify.artifact_class}
        class_rule = next(
            c for c in self._oracle.artifact_classes if c.name == classify.artifact_class
        )

        # Steps 4-5 (deterministic sweeps).
        deterministic: list[CandidateFinding] = [
            *self._consistency.check(artifact_text),
            *self._sq_checker.check(artifact_text),
            *self._decision_gate.check(request.artifact_repo),
        ]
        if self._oracle.oak_conformance is not None:
            deterministic.extend(
                LabelConformanceChecker(self._oracle.oak_conformance).check(artifact_text)
            )

        # Step 6: doctrine sweep, one call per row (skipped for synthesis-only classes).
        if not class_rule.source_quality_only:
            for row in self._oracle.rows:
                disposition = await self._call(
                    sweep,
                    CallKind.ROW,
                    RowDisposition,
                    {
                        **class_context,
                        "row": row,
                        "row_rendered": render_oracle_rows([row]),
                    },
                    ref=row.id,
                )
                if disposition.row_id != row.id:
                    disposition = disposition.model_copy(update={"row_id": row.id})
                sweep.rows[row.id] = disposition

            # Step 7: claims + gates.
            claims_result = await self._call(sweep, CallKind.CLAIMS, ClaimsResult, class_context)
            sections = self._sections_present(artifact_text, classify)
            deterministic.extend(
                self._gate_checker.findings_for(
                    claims_result.claims, classify.artifact_class, sections
                )
            )

            # Step 8: judgement sweeps (steps 7-9 of the spec protocol).
            judgement = await self._call(
                sweep, CallKind.JUDGEMENT, JudgementSweepResult, class_context
            )
            sweep.judgement = judgement

        source_quality = await self._call(
            sweep, CallKind.SOURCE_QUALITY, SourceQualityResult, class_context
        )
        sweep.source_quality = source_quality

        # Verification/repair loop: assemble -> synthesize -> verify -> repair subset.
        review_failed_reason: str | None = None
        findings: list[Finding] = []
        for attempt in range(self._settings.max_regeneration_attempts + 1):
            findings = self._assemble_findings(sweep, deterministic, mode)
            synthesis = await self._call(
                sweep,
                CallKind.SYNTHESIS,
                SynthesisResult,
                {
                    **class_context,
                    "findings_summary": [
                        {"severity": Severity.parse(f.severity).name, "summary": f.summary}
                        for f in findings
                    ],
                },
            )
            sweep.synthesis = synthesis

            defects = self._verifier.check(
                findings,
                self._all_entries(sweep),
                sweep.evidence,
                self._prose_blocks(sweep, findings),
            )
            if not defects:
                break
            if attempt >= self._settings.max_regeneration_attempts:
                review_failed_reason = (
                    "incomplete verification log after "
                    f"{attempt + 1} attempts: " + "; ".join(defects[:5])
                )
                break
            # Repair only the calls implicated by the defects.
            defective_rows = {
                row_id
                for row_id in sweep.rows
                for defect in defects
                if f" {row_id} " in f" {defect} "
            }
            for row_id in sorted(defective_rows):
                row = self._oracle.row(row_id)
                repaired = await self._call(
                    sweep,
                    CallKind.ROW,
                    RowDisposition,
                    {
                        **class_context,
                        "row": row,
                        "row_rendered": render_oracle_rows([row]),
                    },
                    ref=row_id,
                    repair_defects=[d for d in defects if row_id in d],
                )
                sweep.rows[row_id] = repaired

        # Escalation + report.
        result = ReviewResult(
            mode=mode,
            mode_uncertain=mode_uncertain,
            artifact_class=classify.artifact_class,
            findings=findings,
        )
        escalation = self._escalator.check(state, result)
        result = result.model_copy(update={"escalation": escalation})

        final_synthesis = sweep.synthesis
        assert final_synthesis is not None
        current_cycle = state.cycle + 1
        report = ReviewReport(
            artifact_id=request.artifact_id,
            oracle_id=self._oracle.oracle_id,
            oracle_version=self._oracle.version,
            cycle=current_cycle,
            verdict=Verdict(
                mode=mode,
                mode_uncertain=mode_uncertain,
                confidence=final_synthesis.confidence,
                headline=self._guard.launder_prose(final_synthesis.headline),
            ),
            findings=findings,
            row_dispositions=[sweep.rows[k] for k in sorted(sweep.rows)],
            what_survives=self._guard.launder_all(final_synthesis.what_survives),
            residual_risks=self._guard.launder_all(final_synthesis.residual_risks),
            scope_items=sweep.judgement.scope if sweep.judgement else [],
            verification_log=self._all_entries(sweep),
            escalation=escalation,
        )
        rendered = self._renderer.render(report)

        if review_failed_reason is not None:
            # Per spec: failed, regenerated up to budget, never softened or published.
            raise ReviewFailedError(review_failed_reason)

        # Step 13: write state + audit bundle.
        new_record = CycleRecord(
            cycle=current_cycle,
            completed_at=self._clock.now(),
            mode=mode,
            artifact_class=classify.artifact_class,
            content_sha256=content_sha256(artifact_text),
            findings=[
                HistoricalFinding(
                    key=f.key, severity=f.severity, disposition="open", summary=f.summary
                )
                for f in findings
            ],
        )
        new_state = state.model_copy(
            update={"cycle": current_cycle, "history": [*state.history, new_record]}
        )
        state_path = self._state_store.save(new_state, rendered)
        self._write_audit_bundle(request, current_cycle, sweep)

        return PipelineOutcome(
            report=report,
            result=result,
            rendered=rendered,
            state_path=str(state_path),
        )

    def _write_audit_bundle(self, request: ReviewRequest, cycle: int, sweep: _SweepState) -> None:
        bundle_dir = self._settings.review_log_dir / request.artifact_id / f"cycle-{cycle}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "calls.json").write_text(
            json.dumps(sweep.calls, indent=2, default=str) + "\n", encoding="utf-8"
        )
        (bundle_dir / "tool-evidence.json").write_text(
            json.dumps([e.model_dump() for e in sweep.evidence], indent=2) + "\n",
            encoding="utf-8",
        )


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


__all__ = [
    "PipelineOutcome",
    "ReviewPipeline",
    "read_artifact",
    "resolve_artifact_id",
]
