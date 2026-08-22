"""OfflineLLMClient: schema-valid null judgement for --offline runs.

An offline review still runs every deterministic check (consistency, source quality,
label conformance, decision gate, state, escalation) — the LLM sweeps return honest
"no judgement available" payloads and the run is forced advisory by fail-closed mode.
"""

from __future__ import annotations

from typing import Any

from creative_agent.harness.llm.base import AssembledPrompt, CallKind, RawLLMResult
from creative_agent.models.oracle import OracleTable

# Only used when no oracle is supplied (direct construction in a contract test).
_FALLBACK_CLASS = "unclassified"

_OFFLINE_NOTE = "offline mode: no LLM judgement performed"


def _payload_for(prompt: AssembledPrompt, artifact_class: str) -> dict[str, Any]:
    kind = prompt.kind
    if kind is CallKind.CLASSIFY:
        return {
            "artifact_class": artifact_class,
            "mode_recommendation": "advisory",
            "conformance_evidence": None,
            "sections_present": [],
            "rationale": _OFFLINE_NOTE,
        }
    if kind is CallKind.ROW:
        return {
            "row_id": prompt.ref,
            "verdict": "not_applicable",
            "na_reason": _OFFLINE_NOTE,
            "evidence_quotes": [],
            "findings": [],
            "verification_entries": [],
        }
    if kind is CallKind.CLAIMS:
        return {"claims": []}
    if kind is CallKind.SOURCE_QUALITY:
        return {
            "load_bearing_violations": [],
            "regime_break_findings": [],
            "verification_entries": [],
        }
    if kind is CallKind.JUDGEMENT:
        return {"baselines": [], "falsifiability": [], "scope": [], "verification_entries": []}
    return {
        "headline": "Offline review: deterministic checks only; no design judgement.",
        # No judgement was performed, so the verdict cannot claim certainty.
        "confidence": "Guessing",
        "what_survives": [],
        "residual_risks": [
            "LLM judgement sweeps were skipped (offline); doctrinal misses may exist."
        ],
    }


class OfflineLLMClient:
    """Null-judgement client. The artifact class comes from the oracle, never a literal,
    so any corpus can run offline."""

    def __init__(self, oracle: OracleTable | None = None) -> None:
        self._artifact_class = (
            oracle.default_artifact_class() if oracle is not None else _FALLBACK_CLASS
        )

    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        return RawLLMResult(
            kind=prompt.kind,
            payload=_payload_for(prompt, self._artifact_class),
            model="offline",
        )
