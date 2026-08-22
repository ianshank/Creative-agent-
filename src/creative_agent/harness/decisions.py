"""CONFIRM-FIRST decision gating.

DEC-S1..S6 (and any oracle's required_decisions) are obligations of the REVIEWED
artifact's repository — checked in its decision log when the artifact claims conformance
and `--artifact-repo` is supplied. They never gate this harness's own build (that would
be the category error flagged in peer review; the harness has no reward function).
"""

from __future__ import annotations

import re
from pathlib import Path

from creative_agent.models.findings import SupportRef
from creative_agent.models.oracle import (
    DEFAULT_DECISION_ENTRY_PATTERN,
    DecisionLogGrammar,
    OracleTable,
)
from creative_agent.models.sweeps import CandidateFinding

# Decision gating is not a measurement gate; it rides the gate_failure support kind under
# a declared pseudo-gate so support refs stay cross-validatable.
_DECISION_PSEUDO_GATE = "decision"


class DecisionLog:
    """Parses decision entries from a markdown log using the oracle's grammar.

    The heading shape, id prefix, and status vocabulary all vary by shop (DEC-/ADR-,
    CONFIRMED/Accepted), so the pattern is data.
    """

    @staticmethod
    def parse(path: Path, grammar: DecisionLogGrammar | None = None) -> dict[str, str]:
        if not path.exists():
            return {}
        rules = grammar or DecisionLogGrammar(
            entry_pattern=DEFAULT_DECISION_ENTRY_PATTERN, confirmed_status="CONFIRMED"
        )
        pattern = re.compile(rules.entry_pattern, re.MULTILINE)
        text = path.read_text(encoding="utf-8")
        return {m.group("id"): m.group("status") for m in pattern.finditer(text)}


class DecisionGate:
    """Synthesizes deterministic findings for missing/pending required decisions."""

    def __init__(self, oracle: OracleTable, decision_log_filename: str) -> None:
        self._oracle = oracle
        self._filename = decision_log_filename

    def check(self, artifact_repo: Path | None) -> list[CandidateFinding]:
        if artifact_repo is None or not self._oracle.required_decisions:
            return []
        grammar = self._oracle.decision_log_grammar
        entries = DecisionLog.parse(artifact_repo / self._filename, grammar)
        traps = {t.decision_id: t.trap for t in self._oracle.decision_traps}
        severity = self._oracle.protocol.missing_decision_severity
        candidates: list[CandidateFinding] = []
        for decision_id in self._oracle.required_decisions:
            status = entries.get(decision_id)
            if status == grammar.confirmed_status:
                continue
            state = status or "missing"
            trap_note = f" Note: {traps[decision_id]}" if decision_id in traps else ""
            candidates.append(
                CandidateFinding(
                    severity=severity,
                    summary=(
                        f"Required decision {decision_id} is {state} in the artifact "
                        f"repo's {self._filename}; per CONFIRM-FIRST, implement only to "
                        f"a failing stub until it is logged.{trap_note}"
                    ),
                    anchor=f"{decision_id}-missing-decision",
                    supports=[SupportRef(kind="gate_failure", ref=_DECISION_PSEUDO_GATE)],
                    disposition_required=(
                        f"Log {decision_id} (status CONFIRMED) in {self._filename} "
                        "before mechanism build-out."
                    ),
                )
            )
        return candidates
