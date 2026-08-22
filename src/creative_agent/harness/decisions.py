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
from creative_agent.models.oracle import OracleTable
from creative_agent.models.sweeps import CandidateFinding

_ENTRY = re.compile(
    r"^#{1,6}\s+(?P<id>DEC-[A-Za-z0-9]+)\b.*?\b(?P<status>CONFIRMED|DEFERRED|PENDING)\b",
    re.MULTILINE,
)


class DecisionLog:
    """Parses `## DEC-XY — title — STATUS` style entries from a markdown log."""

    @staticmethod
    def parse(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        return {m.group("id"): m.group("status") for m in _ENTRY.finditer(text)}


class DecisionGate:
    """Synthesizes deterministic findings for missing/pending required decisions."""

    def __init__(self, oracle: OracleTable, decision_log_filename: str) -> None:
        self._oracle = oracle
        self._filename = decision_log_filename

    def check(self, artifact_repo: Path | None) -> list[CandidateFinding]:
        if artifact_repo is None or not self._oracle.required_decisions:
            return []
        entries = DecisionLog.parse(artifact_repo / self._filename)
        traps = {t.decision_id: t.trap for t in self._oracle.decision_traps}
        severity = self._oracle.protocol.missing_decision_severity
        candidates: list[CandidateFinding] = []
        for decision_id in self._oracle.required_decisions:
            status = entries.get(decision_id)
            if status == "CONFIRMED":
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
                    supports=[SupportRef(kind="gate_failure", ref=decision_id)],
                    disposition_required=(
                        f"Log {decision_id} (status CONFIRMED) in {self._filename} "
                        "before mechanism build-out."
                    ),
                )
            )
        return candidates
