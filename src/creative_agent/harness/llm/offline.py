"""OfflineLLMClient: schema-valid null judgement for --offline runs.

An offline review still runs every deterministic check (consistency, source quality,
label conformance, decision gate, state, escalation) — the LLM sweeps return honest
"no judgement available" payloads and the run is forced advisory by fail-closed mode.
"""

from __future__ import annotations

from typing import Any

from creative_agent.harness.llm.base import AssembledPrompt, CallKind, RawLLMResult

_OFFLINE_NOTE = "offline mode: no LLM judgement performed"


def _payload_for(prompt: AssembledPrompt) -> dict[str, Any]:
    kind = prompt.kind
    if kind is CallKind.CLASSIFY:
        return {
            "artifact_class": "architecture_design",
            "mode_recommendation": "advisory",
            "conformance_evidence": None,
            "safety_section_present": False,
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
        "confidence": "Certain",
        "what_survives": [],
        "residual_risks": [
            "LLM judgement sweeps were skipped (offline); doctrinal misses may exist."
        ],
    }


class OfflineLLMClient:
    async def generate(self, prompt: AssembledPrompt) -> RawLLMResult:
        return RawLLMResult(kind=prompt.kind, payload=_payload_for(prompt), model="offline")
