"""Artifact classification and the fail-closed mode rule (DEC-F40).

Extracted from `ReviewPipeline._run`, which is 350 lines carrying ten spec steps inline.
Line count is not the reason. These three decisions are the harness's *security* decisions
about what kind of review this is, and none of them had a seam:

- **The fail-closed mode rule (DEC-F6).** Conformance mode removes the advisory severity
  ceiling — it is the difference between a document's Blocker being published as a Blocker
  and being capped at Info. The rule is a three-way conjunction plus an independent marker
  scan, which is twelve meaningful input combinations, and the only way to reach it was a
  full `pipeline.run()` against a scripted fake. The integration suite covered whichever
  combinations its six-key script happened to hit; the truth table was enumerated nowhere.
- **Required sections.** Read from the artifact's own headings, never from the model's
  claim, because honouring the model's word would let the reviewer of an untrusted
  artifact clear a safety Blocker by asserting the section exists.
- **The artifact-class lookup**, which must fail as a typed error rather than a
  `StopIteration` that the CLI reports as exit 5 (DEC-F24).

Nothing here calls the LLM. That is the boundary: `ReviewPipeline` keeps the probe-and-
re-probe loop, because it makes calls and spends budget, and hands the answers here.
"""

from __future__ import annotations

import logging
import re

from creative_agent.errors import LLMOutputError
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.models.oracle import ArtifactClassRule, OracleTable
from creative_agent.models.review import ModeSelection, ReviewMode
from creative_agent.models.sweeps import ClassifyResult

_LOG = get_logger(__name__)
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+)$", re.MULTILINE)


def squash(text: str) -> str:
    """Collapse whitespace and case, for quoting comparisons.

    An evidence quote is compared against the artifact after squashing so that a model
    re-wrapping a line does not make a true quote look invented — and so that adding
    whitespace cannot make an invented one look true.
    """
    return re.sub(r"\s+", " ", text).strip().casefold()


class ModeResolver:
    """Decides what kind of review this is, from the artifact and the model's classify."""

    def __init__(self, oracle: OracleTable) -> None:
        self._oracle = oracle

    def class_rule(self, artifact_class: str) -> ArtifactClassRule:
        """The oracle rule for a class, as a typed failure rather than a StopIteration.

        `next()` with no default raises `StopIteration`, which inside a coroutine Python
        converts to `RuntimeError` — not a `CreativeAgentError`, so the CLI's typed handler
        misses it and a malformed model response is reported as exit 5 "unexpected error"
        instead of the contractual exit 3 (DEC-F24).
        """
        for rule in self._oracle.artifact_classes:
            if rule.name == artifact_class:
                return rule
        raise LLMOutputError(f"classify returned unknown artifact class {artifact_class!r}")

    def known_classes(self) -> set[str]:
        return {rule.name for rule in self._oracle.artifact_classes}

    def sections_present(self, text: str, classify: ClassifyResult) -> set[str]:
        """Which required sections the artifact actually contains.

        Determined from the document's own headings ONLY. The model also reports what it
        saw, but a required-section obligation is a deterministic gate: honouring the
        model's word would let the reviewer of an untrusted artifact clear a Blocker by
        asserting it. Disagreement is logged, never obeyed.
        """
        headings = {m.group("title").strip().casefold() for m in _HEADING.finditer(text)}
        declared = {
            section for rule in self._oracle.artifact_classes for section in rule.requires_sections
        }
        present = {
            section
            for section in declared
            if any(section.casefold() in heading for heading in headings)
        }
        unsupported = {n for n in classify.sections_present if n in declared} - present
        if unsupported:
            log_event(
                _LOG,
                logging.WARNING,
                "sections.claim_unsupported",
                claimed=sorted(unsupported),
                found=sorted(present),
            )
        return present

    def resolve(
        self, requested: ModeSelection, classify: ClassifyResult, artifact_text: str
    ) -> tuple[ReviewMode, bool]:
        """`(mode, mode_uncertain)` under the fail-closed rule (DEC-F6).

        An explicit `--mode` is obeyed and is never uncertain: the operator has decided.

        Under `auto`, conformance requires **both** that the model recommends it and that
        its `conformance_evidence` is a real quote from the artifact — squashed on both
        sides, so re-wrapping does not break a true quote and added whitespace does not
        rescue an invented one. Everything else resolves to advisory, which caps severities
        at the oracle's advisory ceiling. That direction is the whole point: a reviewer that
        can be talked into the stricter mode by the document it is reviewing has it
        backwards, and a reviewer that publishes an uncapped Blocker on an unverified
        conformance claim is the failure this rule prevents.

        `mode_uncertain` is independent of the mode: it says the artifact *contains*
        conformance markers while the review nonetheless resolved to advisory, which is the
        case a human should look at. It is not a severity and it does not change one; it
        reaches the rendered verdict line so nobody reads "advisory" as "no claim made".
        """
        if requested != "auto":
            return requested, False
        folded = artifact_text.casefold()
        markers_present = any(
            marker.casefold() in folded for marker in self._oracle.conformance.markers
        )
        evidence = (classify.conformance_evidence or "").strip()
        evidence_found = bool(evidence) and squash(evidence) in squash(artifact_text)
        if classify.mode_recommendation == "conformance" and evidence_found:
            return "conformance", False
        return "advisory", markers_present
