"""Internal-consistency sweep (spec protocol step 6) — deterministic.

Extracts symbol definitions from the artifact and flags a symbol defined twice with
different right-hand sides: "two definitions of one quantity is a Blocker". Conservative
by design — only unambiguous definition syntax counts, so a hit is a real contradiction
candidate rather than prose noise.
"""

from __future__ import annotations

import re
from collections import defaultdict

from creative_agent.models.findings import SupportRef
from creative_agent.models.sweeps import CandidateFinding

# Definition shapes recognized, tightest first:
#   gamma := 0.99          alpha_t = eta / ||g||^2        `beta` = 0.9
#   let tau be the horizon          define R as the discounted return
_ASSIGN = re.compile(
    r"^\s*`?(?P<symbol>[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]{0,19})`?\s*(?::=|≜|=)\s*(?P<rhs>\S.*)$"
)
_VERBAL = re.compile(
    r"\b(?:let|define)\s+`?(?P<symbol>[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]{0,19})`?\s+"
    r"(?:be|as|denote)\s+(?P<rhs>[^.;\n]{3,120})",
    re.IGNORECASE,
)
# Assignment lines that are prose, not definitions (e.g. "Note = important").
_PROSE_SYMBOLS = frozenset(
    {"note", "example", "result", "verdict", "status", "summary", "title", "name"}
)


def _normalize_rhs(rhs: str) -> str:
    return re.sub(r"\s+", " ", rhs.strip().rstrip(".;,")).casefold()


def extract_definitions(text: str) -> dict[str, list[str]]:
    """Map symbol -> distinct normalized definitions found, in order of appearance."""
    definitions: dict[str, list[str]] = defaultdict(list)
    in_code_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        for pattern in (_ASSIGN, _VERBAL):
            match = pattern.search(line)
            if match:
                symbol = match.group("symbol")
                if symbol.casefold() in _PROSE_SYMBOLS:
                    continue
                rhs = _normalize_rhs(match.group("rhs"))
                if rhs and rhs not in definitions[symbol]:
                    definitions[symbol].append(rhs)
                break
    return dict(definitions)


class ConsistencyChecker:
    """Duplicate-definition detection feeding the internal_contradiction blocker basis."""

    def check(self, text: str) -> list[CandidateFinding]:
        candidates: list[CandidateFinding] = []
        for symbol, rhs_list in sorted(extract_definitions(text).items()):
            if len(rhs_list) > 1:
                candidates.append(
                    CandidateFinding(
                        severity="blocker",
                        summary=(
                            f"Internal contradiction: symbol '{symbol}' is defined "
                            f"{len(rhs_list)} different ways: " + " | ".join(rhs_list[:3])
                        ),
                        anchor=f"duplicate-definition-{symbol}",
                        supports=[SupportRef(kind="internal_contradiction", ref=symbol)],
                        disposition_required=(
                            "Reconcile the definitions; two definitions of one quantity "
                            "is a Blocker."
                        ),
                    )
                )
        return candidates
