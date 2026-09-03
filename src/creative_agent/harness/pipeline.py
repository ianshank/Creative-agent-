"""ReviewPipeline: the multi-call orchestration with deterministic assembly.

The LLM answers typed calls (classify, per-row sweeps, claims, source-quality,
judgement, synthesis); every structural rule — severity caps, gate scoring, verification
completeness, tool honesty, escalation, rendering — is enforced here, deterministically.
The findings table is assembled by this module, never emitted whole by the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from creative_agent.config import HarnessSettings
from creative_agent.errors import (
    LLMOutputError,
    LLMTimeoutError,
    ReviewFailedError,
)
from creative_agent.harness.artifact import (
    content_sha256,
    read_artifact,
    resolve_artifact_id,
    validate_artifact_path,
)
from creative_agent.harness.budget import RunBudget
from creative_agent.harness.classification import ModeResolver
from creative_agent.harness.consistency import ConsistencyChecker
from creative_agent.harness.decisions import DecisionGate
from creative_agent.harness.gates import MeasurementGateChecker
from creative_agent.harness.llm.base import (
    AssembledPrompt,
    CallKind,
    RawLLMResult,
    ToolEvidence,
    parse_payload,
)
from creative_agent.harness.logging import get_logger, log_event, timed_stage
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
    ScopeItem,
    SourceQualityResult,
    SynthesisResult,
)
from creative_agent.models.verification import VerificationEntry

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+)$", re.MULTILINE)
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_LOG = get_logger(__name__)


@dataclass
class _SweepState:
    """Mutable working set across the verification/repair loop."""

    rows: dict[str, RowDisposition] = field(default_factory=dict)
    synthesis: SynthesisResult | None = None
    source_quality: SourceQualityResult | None = None
    judgement: JudgementSweepResult | None = None
    evidence: list[ToolEvidence] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)
    # The run's spend cap, carried with the sweep because it is per-run state like the
    # rest of this dataclass. `calls` stays as the audit record written to the bundle;
    # the budget no longer has to be re-derived from it on every call (DEC-F41).
    budget: RunBudget = field(default_factory=lambda: RunBudget(None))


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
        self._modes = ModeResolver(oracle)
        self._severity_policy = SeverityPolicy(oracle)
        self._gate_checker = MeasurementGateChecker(oracle)
        self._sq_checker = SourceQualityChecker(oracle.source_quality)
        self._consistency = ConsistencyChecker(oracle.consistency)
        self._verifier = VerificationLogChecker(
            oracle.source_author_names(),
            fetch_tools=frozenset(settings.fetch_tool_names),
            impersonation_patterns=tuple(oracle.attribution.impersonation_patterns),
            identifier_authorities={
                scheme: tuple(hosts)
                for scheme, hosts in settings.identifier_authority_hosts.items()
            },
        )
        self._decision_gate = DecisionGate(oracle, settings.decision_log_filename)
        self._escalator = CycleEscalator(oracle)
        self._guard = ThreatGuard(
            oracle,
            settings.max_prose_chars,
            blocked_host_suffixes=tuple(settings.blocked_host_suffixes),
            allow_internal_fetch_hosts=settings.allow_internal_fetch_hosts,
            # One setting, both halves (DEC-F25): the registrars that may vouch for an
            # identifier are exactly the hosts a review must be able to fetch to produce
            # that evidence. Before this, naming a mirror let it vouch for an identifier
            # the hook would then refuse to fetch.
            identifier_authority_hosts=settings.identifier_authority_hosts,
        )
        self._assembler = PromptAssembler(settings.prompt_search_paths, agent.prompt_template_dir())
        self._renderer = OutputRenderer(oracle.protocol.unverified_marker)

    # ---- LLM call helper -------------------------------------------------

    async def _generate_within_timeout(
        self, prompt: AssembledPrompt, *, kind: CallKind, ref: str
    ) -> RawLLMResult:
        """Run one provider call under `llm_timeout_seconds`.

        The setting was declared in `HarnessSettings` and documented in
        `config/settings.example.yaml` and used nowhere in `src/` — dead configuration
        until now (DEC-F17). A hung call previously blocked the review indefinitely.
        """
        timeout = self._settings.llm_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                return await self._llm.generate(prompt)
        except TimeoutError as exc:
            log_event(
                _LOG,
                logging.ERROR,
                "llm.call_timeout",
                kind=kind.value,
                ref=ref,
                timeout_seconds=timeout,
            )
            raise LLMTimeoutError(
                f"{kind.value} call exceeded llm_timeout_seconds ({timeout}s); the review "
                "was stopped before producing a verdict and nothing was published"
            ) from exc

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
        for attempt in range(attempts):
            prompt = self._assembler.assemble(
                kind,
                ref=ref,
                allowed_tools=self._settings.agent_tools,
                fetch_domain_allowlist=context.get("fetch_domains", []),  # type: ignore[arg-type]
                allowed_read_roots=context.get("read_roots", []),  # type: ignore[arg-type]
                context={**context, "repair_defects": defects},
            )
            prompt = prompt.model_copy(update={"remaining_budget_usd": sweep.budget.remaining()})
            # Budget is checked *inside* the attempt loop, before each provider call, so a
            # single logical call cannot burn several times the remaining budget: `_call`
            # retries up to max_regeneration_attempts + 1 times and every attempt reaches
            # the wire (DEC-F17).
            sweep.budget.check(kind=kind.value, ref=ref)
            with timed_stage(
                _LOG, "llm.call", kind=kind.value, ref=ref, attempt=attempt + 1
            ) as call_log:
                raw: RawLLMResult = await self._generate_within_timeout(prompt, kind=kind, ref=ref)
                call_log["model"] = raw.model
                call_log["cost_usd"] = raw.cost_usd
                call_log["tool_results"] = len(raw.tool_evidence)
            sweep.evidence.extend(raw.tool_evidence)
            sweep.calls.append(
                {"kind": kind.value, "ref": ref, "model": raw.model, "cost_usd": raw.cost_usd}
            )
            sweep.budget.record(raw.cost_usd)
            try:
                return parse_payload(raw, model_type)
            except ValidationError as exc:
                last_error = str(exc)
                first = exc.errors()[0]
                log_event(
                    _LOG,
                    logging.WARNING,
                    "llm.schema_violation",
                    kind=kind.value,
                    ref=ref,
                    attempt=attempt + 1,
                    location=".".join(str(part) for part in first["loc"]),
                    reason=first["msg"],
                )
                defects = [*defects, f"schema violation: {first['msg']}"]
        raise LLMOutputError(
            f"{kind.value} call failed schema validation after {attempts} attempts: {last_error}"
        )

    # ---- deterministic helpers ------------------------------------------

    def _candidate_to_finding(
        self,
        candidate: CandidateFinding,
        index: int,
        default_row: str | None = None,
        origin: str = "llm",
    ) -> Finding:
        known_rows = {row.id for row in self._oracle.rows}
        # `GatePolicy.all_gate_refs` exists precisely so gate references "can be
        # cross-validated instead of being free-form strings" — its own comment — and until
        # now it had zero call sites. Only `doctrine_row` supports were filtered, so a
        # `gate_failure` support carried whatever `ref` the model wrote. That is a severity
        # lever, not cosmetic: `SeverityPolicy` counts any `gate_failure` support as a
        # blocker basis and drops the tier cap the moment one non-doctrine support exists,
        # so naming a gate that does not exist promoted a Major to a Blocker and exit 2.
        # The model writes this field and the artifact under review can steer the model,
        # which is the threat model DEC-F9 assumes (DEC-F31).
        known_gates = self._oracle.gate_policy.all_gate_refs()
        doctrine_refs = [r for r in candidate.doctrine_refs if r in known_rows]
        if not doctrine_refs and default_row:
            doctrine_refs = [default_row]
        supports = [
            s
            for s in candidate.supports
            if (s.kind != "doctrine_row" or s.ref in known_rows)
            and (s.kind != "gate_failure" or s.ref in known_gates)
        ]
        gate_refs = [g for g in candidate.gate_refs if g in known_gates]
        dropped = (
            len(candidate.supports) - len(supports) + len(candidate.gate_refs) - len(gate_refs)
        )
        if dropped:
            # An unknown reference is the model inventing doctrine, and a reviewer reading
            # only the report would never see that it was dropped. Counts, never the
            # strings: the refs are model prose (DEC-F10).
            log_event(
                _LOG,
                logging.WARNING,
                "findings.unknown_refs_dropped",
                origin=origin,
                dropped=dropped,
                anchor_slug=normalize_slug(candidate.anchor) or "finding",
            )
        key_row = doctrine_refs[0] if doctrine_refs else self._oracle.protocol.placeholder_row_id
        return Finding(
            finding_id=f"F{index}",
            origin=origin,
            severity=candidate.severity,
            original_severity=candidate.severity,
            summary=self._guard.launder_prose(candidate.summary),
            doctrine_refs=doctrine_refs,
            gate_refs=gate_refs,
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
        with timed_stage(
            _LOG,
            "review",
            artifact_id=request.artifact_id,
            oracle=self._oracle.oracle_id,
            agent=request.agent_name,
        ) as review_log:
            outcome = await self._run(request, review_log)
        return outcome

    async def _run(self, request: ReviewRequest, review_log: dict[str, object]) -> PipelineOutcome:
        state = self._state_store.load(request.artifact_id)
        # One read per review when the caller already did it (DEC-F23): the CLI must read
        # the artifact to derive its id from front matter, and re-reading here meant the
        # id and the content hash written into state could come from two different
        # versions of the file. A caller that supplies only a path still gets the read —
        # and the same containment check, since a reviewed worktree is untrusted and git
        # carries symlinks, so a path resolving outside the repository is refused.
        artifact_text = request.artifact_text
        if artifact_text is None:
            artifact_text = read_artifact(
                request.artifact_path,
                self._settings.max_artifact_bytes,
                containment_root=request.artifact_repo,
            )
        else:
            # Read once, checked twice. The check must not travel with the read, or
            # removing it from the CLI would remove it from the product: the pipeline is
            # the layer that hands the text to the model, so it is the layer that must
            # refuse a path it would not have read itself.
            validate_artifact_path(
                request.artifact_path,
                self._settings.max_artifact_bytes,
                containment_root=request.artifact_repo,
            )
        fetch_domains = self._guard.fetch_domain_allowlist(artifact_text)
        rejected_hosts = self._guard.rejected_fetch_hosts(artifact_text)
        read_roots = self._guard.allowed_read_roots(
            request.artifact_path, self._settings.oracle_search_paths, request.artifact_repo
        )
        if rejected_hosts:
            # Not fatal, but a planted internal URL is exactly what the threat model is
            # for — make it visible rather than silently dropping it.
            log_event(
                _LOG,
                logging.WARNING,
                "security.fetch_hosts_rejected",
                artifact_id=request.artifact_id,
                hosts=rejected_hosts,
            )
        delimited = ThreatGuard.delimit_artifact(artifact_text)
        prior_keys = sorted(state.open_major_keys())
        log_event(
            _LOG,
            logging.INFO,
            "review.started",
            artifact_id=request.artifact_id,
            prior_cycles=state.cycle,
            artifact_bytes=len(artifact_text),
            fetch_domains=len(fetch_domains),
            prior_open_major_keys=len(prior_keys),
        )

        base_context: dict[str, object] = {
            "oracle": self._oracle,
            "artifact": delimited,
            "fetch_domains": fetch_domains,
            "read_roots": read_roots,
            "prior_keys": prior_keys,
            **self._agent.build_context(request, self._oracle, state),
        }

        sweep = _SweepState(budget=RunBudget(self._settings.max_budget_usd))

        # Step 3: classify + fail-closed mode (with one re-probe).
        classify = await self._call(sweep, CallKind.CLASSIFY, ClassifyResult, base_context)
        classify = await self._validated_class(sweep, classify, base_context)
        mode, mode_uncertain = self._modes.resolve(request.mode, classify, artifact_text)
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
            mode, mode_uncertain2 = self._modes.resolve(request.mode, reprobe, artifact_text)
            mode_uncertain = mode == "advisory" and mode_uncertain2
            # The same validator as the first probe. Assigning `reprobe` unchecked meant a
            # model that answered validly once and invalidly on the re-probe reached the
            # lookup below with an unknown class, where a bare `next()` raised
            # StopIteration inside a coroutine — RuntimeError, not a CreativeAgentError, so
            # the CLI's typed handler missed it and a malformed model response was reported
            # as exit 5 "unexpected error" instead of exit 3 (DEC-F24).
            classify = await self._validated_class(sweep, reprobe, base_context)

        class_context = {**base_context, "mode": mode, "artifact_class": classify.artifact_class}
        class_rule = self._modes.class_rule(classify.artifact_class)

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
            sections = self._modes.sections_present(artifact_text, classify)
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
                log_event(
                    _LOG,
                    logging.ERROR,
                    "verification.failed",
                    artifact_id=request.artifact_id,
                    attempts=attempt + 1,
                    defect_count=len(defects),
                    defects=defects[:5],
                )
                break
            # Repair only the calls implicated by the defects. Row ids are matched on a
            # word boundary so D1's repair never inherits D10's defects.
            defects_by_row = {
                row_id: [d for d in defects if _mentions_row(d, row_id)] for row_id in sweep.rows
            }
            defective_rows = sorted(row_id for row_id, matched in defects_by_row.items() if matched)
            if not defective_rows:
                # Nothing the repair loop can act on (tool-honesty or attribution
                # defects, or a class with no row sweeps). Re-running the same calls
                # would spend the budget to reach the identical outcome.
                review_failed_reason = (
                    "verification log defects with no repairable call: " + "; ".join(defects[:5])
                )
                log_event(
                    _LOG,
                    logging.ERROR,
                    "verification.unrepairable",
                    artifact_id=request.artifact_id,
                    defect_count=len(defects),
                    defects=defects[:5],
                )
                break
            log_event(
                _LOG,
                logging.INFO,
                "verification.repairing",
                artifact_id=request.artifact_id,
                attempt=attempt + 1,
                defect_count=len(defects),
                rows=defective_rows,
            )
            for row_id in defective_rows:
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
                    repair_defects=defects_by_row[row_id],
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
        if escalation is not None:
            log_event(
                _LOG,
                logging.WARNING,
                "escalation.charter_review",
                artifact_id=request.artifact_id,
                key=escalation.key.render(),
                cycles=escalation.cycles,
            )

        final_synthesis = sweep.synthesis
        if final_synthesis is None:
            # Unreachable on every path today — synthesis is assigned before this point —
            # but an `assert` is the wrong guard for a None dereference: `python -O`
            # removes it, and the lines below would then raise an AttributeError, which is
            # not a CreativeAgentError and would therefore be reported as exit 5.
            raise LLMOutputError("synthesis produced no result; nothing to publish")
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
            row_dispositions=[
                sweep.rows[row.id] for row in self._oracle.rows if row.id in sweep.rows
            ],
            what_survives=self._guard.launder_all(final_synthesis.what_survives),
            residual_risks=self._guard.launder_all(final_synthesis.residual_risks),
            scope_items=self._scope_items(sweep),
            verification_log=self._all_entries(sweep),
            escalation=escalation,
        )
        # DEC-F22: launder the assembled report, not a list of fields. Twice now the field
        # list has been the defect — DEC-F16 missed two fields and DEC-F19 missed two more,
        # among them the verification log's `assertion` and the doctrine sweep's
        # `na_reason`. This walks every string on the report and excludes the structural
        # ids instead, so a prose field added tomorrow is covered tomorrow. The per-field
        # laundering above is left in place deliberately: findings are laundered where they
        # are built because their text also reaches review state and the audit bundle,
        # neither of which passes through here.
        report = self._guard.launder_model(report)

        if review_failed_reason is not None:
            # Checked before rendering: on this path the report is discarded, and building
            # and rendering it first was work whose only effect was to suggest to the next
            # reader that the rendered text is available to the error handler.
            raise ReviewFailedError(review_failed_reason)

        rendered = self._renderer.render(report)

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
        # Pass the cycle we loaded so the store can refuse a lost update (DEC-F14). The
        # findings, the escalation verdict and `rendered` above were all computed from
        # that snapshot; if a concurrent review has advanced it, publishing this run would
        # discard their history and, worse, would act on a recurrence count that is no
        # longer true. `StateConflictError` aborts the run without writing anything.
        state_path = self._state_store.save(new_state, rendered, expected_cycle=state.cycle)
        self._write_audit_bundle(request, current_cycle, sweep)

        severity_counts: dict[str, int] = {}
        for finding in findings:
            name = Severity.parse(finding.severity).name
            severity_counts[name] = severity_counts.get(name, 0) + 1
        review_log.update(
            {
                "cycle": current_cycle,
                "mode": mode,
                "artifact_class": classify.artifact_class,
                "findings": len(findings),
                "severities": severity_counts,
                "llm_calls": len(sweep.calls),
                "escalated": escalation is not None,
            }
        )
        log_event(
            _LOG,
            logging.INFO,
            "review.completed",
            artifact_id=request.artifact_id,
            cycle=current_cycle,
            mode=mode,
            findings=len(findings),
            severities=severity_counts,
            state_path=str(state_path),
        )

        return PipelineOutcome(
            report=report,
            result=result,
            rendered=rendered,
            state_path=str(state_path),
        )

    async def _validated_class(
        self,
        sweep: _SweepState,
        classify: ClassifyResult,
        context: dict[str, object],
    ) -> ClassifyResult:
        """Return a classify result whose `artifact_class` is one the oracle defines.

        One re-probe naming the permitted set, then a typed failure. Both classify probes
        route through this: the first probe had the check inline and the mode re-probe had
        none, which is the shape of defect DEC-F24 is about — a value the model chose,
        validated at the call site that happened to be written first.
        """
        known = self._modes.known_classes()
        if classify.artifact_class in known:
            return classify
        retried = await self._call(
            sweep,
            CallKind.CLASSIFY,
            ClassifyResult,
            context,
            repair_defects=[
                f"artifact_class must be one of {sorted(known)}, got {classify.artifact_class!r}"
            ],
        )
        if retried.artifact_class not in known:
            raise LLMOutputError(
                f"classify returned unknown artifact class {retried.artifact_class!r}"
            )
        return retried

    def _scope_items(self, sweep: _SweepState) -> list[ScopeItem]:
        """Scope items as the judgement returned them; laundering is DEC-F22's boundary."""
        return list(sweep.judgement.scope) if sweep.judgement is not None else []

    def _write_audit_bundle(self, request: ReviewRequest, cycle: int, sweep: _SweepState) -> None:
        bundle_dir = self._settings.review_log_dir / request.artifact_id / f"cycle-{cycle}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        # newline="\n" on every write: the audit bundle and the report are compared
        # byte-for-byte by the golden tests, and a Windows checkout would otherwise
        # emit CRLF and fail them for a reason that has nothing to do with content.
        (bundle_dir / "calls.json").write_text(
            json.dumps(sweep.calls, indent=2, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (bundle_dir / "tool-evidence.json").write_text(
            json.dumps([e.model_dump() for e in sweep.evidence], indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _mentions_row(defect: str, row_id: str) -> bool:
    """Whole-token match so 'D1' does not match a defect that names 'D10'."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(row_id)}(?![A-Za-z0-9])", defect) is not None


__all__ = [
    "PipelineOutcome",
    "ReviewPipeline",
    "read_artifact",
    "resolve_artifact_id",
]
