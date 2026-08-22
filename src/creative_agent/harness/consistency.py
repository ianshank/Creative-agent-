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
from creative_agent.models.oracle import ConsistencyConfig
from creative_agent.models.sweeps import CandidateFinding

# Definition shapes recognized, tightest first:
#   gamma := 0.99          alpha_t = eta / ||g||^2        `beta` = 0.9
#   let tau be the horizon          define R as the discounted return
_SYMBOL_CLASS = "[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]"
# Assignment lines that are prose, not definitions (e.g. "Note = important"). Default for
# English artifacts; oracles override via `consistency.prose_symbols`.
DEFAULT_PROSE_SYMBOLS: tuple[str, ...] = (
    "note",
    "example",
    "result",
    "verdict",
    "status",
    "summary",
    "title",
    "name",
)


def _build_patterns(max_symbol: int, max_definition: int) -> tuple[re.Pattern[str], ...]:
    symbol = f"(?P<symbol>{_SYMBOL_CLASS}{{0,{max_symbol - 1}}})"
    assign = re.compile(rf"^\s*`?{symbol}`?\s*(?::=|≜|=)\s*(?P<rhs>\S.*)$")
    verbal = re.compile(
        rf"\b(?:let|define)\s+`?{symbol}`?\s+"
        rf"(?:be|as|denote)\s+(?P<rhs>[^.;\n]{{3,{max_definition}}})",
        re.IGNORECASE,
    )
    return (assign, verbal)


def _normalize_rhs(rhs: str) -> str:
    return re.sub(r"\s+", " ", rhs.strip().rstrip(".;,")).casefold()


def extract_definitions(
    text: str,
    *,
    prose_symbols: tuple[str, ...] = DEFAULT_PROSE_SYMBOLS,
    max_symbol_length: int = 20,
    max_definition_length: int = 120,
) -> dict[str, list[str]]:
    """Map symbol -> distinct normalized definitions found, in order of appearance."""
    patterns = _build_patterns(max_symbol_length, max_definition_length)
    ignored = {word.casefold() for word in prose_symbols}
    definitions: dict[str, list[str]] = defaultdict(list)
    in_code_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                symbol = match.group("symbol")
                if symbol.casefold() in ignored:
                    continue
                rhs = _normalize_rhs(match.group("rhs"))
                if rhs and rhs not in definitions[symbol]:
                    definitions[symbol].append(rhs)
                break
    return dict(definitions)


class ConsistencyChecker:
    """Duplicate-definition detection feeding the internal_contradiction blocker basis."""

    def __init__(self, config: ConsistencyConfig | None = None) -> None:
        self._config = config or ConsistencyConfig()

    def check(self, text: str) -> list[CandidateFinding]:
        candidates: list[CandidateFinding] = []
        prose = tuple(self._config.prose_symbols) or DEFAULT_PROSE_SYMBOLS
        found = extract_definitions(
            text,
            prose_symbols=prose,
            max_symbol_length=self._config.max_symbol_length,
            max_definition_length=self._config.max_definition_length,
        )
        for symbol, rhs_list in sorted(found.items()):
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
