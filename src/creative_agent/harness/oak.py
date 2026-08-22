"""Label-conformance detection (spec-delta: OaK-conformance requirement).

Deterministic and data-driven: the oracle enumerates the label's required elements with
detection patterns; invoking the label while omitting any element yields a finding naming
that element by number — the label is never accepted as evidence of the structure.
"""

from __future__ import annotations

import re

from creative_agent.models.findings import SupportRef
from creative_agent.models.oracle import LabelElement, OakConformanceSpec
from creative_agent.models.sweeps import CandidateFinding


class LabelConformanceChecker:
    def __init__(self, spec: OakConformanceSpec) -> None:
        self._spec = spec
        self._label = re.compile(spec.label_pattern, re.IGNORECASE)
        self._doctrine_ref = spec.doctrine_ref

    @staticmethod
    def _element_present(element: LabelElement, folded: str) -> bool:
        return any(pattern.casefold() in folded for pattern in element.patterns)

    def check(self, artifact_text: str) -> list[CandidateFinding]:
        if not self._label.search(artifact_text):
            return []
        folded = artifact_text.casefold()
        candidates: list[CandidateFinding] = []
        for group_name, elements in (
            ("feature", self._spec.features),
            ("stage", self._spec.stages),
        ):
            for number, element in enumerate(elements, start=1):
                if not self._element_present(element, folded):
                    candidates.append(
                        CandidateFinding(
                            severity=self._spec.missing_severity,
                            summary=(
                                f"Label invoked but {group_name} {number} is never "
                                f"named: {element.label}. The label is not evidence of "
                                "the structure."
                            ),
                            anchor=f"label-missing-{group_name}-{number}",
                            doctrine_refs=[self._doctrine_ref],
                            supports=[SupportRef(kind="doctrine_row", ref=self._doctrine_ref)],
                            disposition_required=(
                                f"Name {group_name} {number} with its update rule, or "
                                "drop the label."
                            ),
                        )
                    )
        return candidates
