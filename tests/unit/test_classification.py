"""The fail-closed mode rule as a truth table (DEC-F40).

Conformance mode removes the advisory severity ceiling: it is the difference between a
document's Blocker being published as a Blocker and being capped at Info. The rule is a
three-way conjunction plus an independent marker scan — twelve meaningful combinations —
and until `ModeResolver` was extracted the only way to reach it was a full `pipeline.run()`
against a scripted fake. The integration suite covered whichever combinations its six-key
script happened to hit, and the table was enumerated nowhere.

That is the entire argument for the extraction. It is not about line count.
"""

from __future__ import annotations

import logging

import pytest

from creative_agent.errors import CreativeAgentError, LLMOutputError
from creative_agent.harness.classification import ModeResolver, squash
from creative_agent.models.sweeps import ClassifyResult
from tests.factories import make_oracle

MARKER = "Mini Program"
ARTIFACT = (
    f"# Design\n\nThis design conforms to the {MARKER} research corpus.\n\n## Safety\n\nOk.\n"
)
PLAIN = "# Design\n\nA design with no claim of any kind.\n\n## Safety\n\nOk.\n"
QUOTE = f"This design conforms to the {MARKER} research corpus."


def classify(**overrides: object) -> ClassifyResult:
    fields: dict[str, object] = {
        "artifact_class": "architecture_design",
        "mode_recommendation": "conformance",
        "conformance_evidence": QUOTE,
        "sections_present": [],
        "rationale": "test",
    }
    fields.update(overrides)
    return ClassifyResult(**fields)  # type: ignore[arg-type]


@pytest.fixture()
def resolver() -> ModeResolver:
    return ModeResolver(make_oracle())


class TestTheFailClosedModeTable:
    @pytest.mark.parametrize(
        ("recommendation", "evidence", "text", "expected_mode", "expected_uncertain"),
        [
            # The only route to conformance: recommended AND the quote is really there.
            ("conformance", QUOTE, ARTIFACT, "conformance", False),
            # Recommended, but the quote is invented — the forgery this rule exists for.
            (
                "conformance",
                "This design conforms to a corpus never mentioned.",
                ARTIFACT,
                "advisory",
                True,
            ),
            # Recommended with no quote at all.
            ("conformance", None, ARTIFACT, "advisory", True),
            ("conformance", "   ", ARTIFACT, "advisory", True),
            # A real quote, but the model did not recommend conformance.
            ("advisory", QUOTE, ARTIFACT, "advisory", True),
            # Neither: plainly advisory, and no markers, so not even uncertain.
            ("advisory", None, PLAIN, "advisory", False),
            ("advisory", "", PLAIN, "advisory", False),
            # Markers present but nothing else: advisory AND uncertain — the case a human
            # is meant to look at, and the reason `mode_uncertain` is not a severity.
            ("advisory", None, ARTIFACT, "advisory", True),
        ],
    )
    def test_the_table(
        self,
        resolver: ModeResolver,
        recommendation: str,
        evidence: str | None,
        text: str,
        expected_mode: str,
        expected_uncertain: bool,
    ) -> None:
        mode, uncertain = resolver.resolve(
            "auto",
            classify(mode_recommendation=recommendation, conformance_evidence=evidence),
            text,
        )
        assert (mode, uncertain) == (expected_mode, expected_uncertain)

    @pytest.mark.parametrize("requested", ["conformance", "advisory"])
    def test_an_explicit_mode_is_obeyed_and_never_uncertain(
        self, resolver: ModeResolver, requested: str
    ) -> None:
        """The operator has decided; uncertainty is a statement about an automatic choice."""
        assert resolver.resolve(requested, classify(), PLAIN) == (requested, False)  # type: ignore[arg-type]

    def test_a_requoted_line_is_still_a_real_quote(self, resolver: ModeResolver) -> None:
        """Squashing both sides, so re-wrapping a quote does not make it look invented."""
        rewrapped = QUOTE.replace(" ", "\n   ")
        mode, _ = resolver.resolve("auto", classify(conformance_evidence=rewrapped), ARTIFACT)
        assert mode == "conformance"

    def test_added_whitespace_cannot_rescue_an_invented_quote(self, resolver: ModeResolver) -> None:
        """The other direction of the same normalisation, which is the one that matters."""
        mode, _ = resolver.resolve(
            "auto", classify(conformance_evidence="never   appears  here"), ARTIFACT
        )
        assert mode == "advisory"

    def test_marker_matching_is_case_insensitive(self, resolver: ModeResolver) -> None:
        shouty = PLAIN.replace("A design", f"A {MARKER.upper()} design")
        _, uncertain = resolver.resolve(
            "auto", classify(mode_recommendation="advisory", conformance_evidence=None), shouty
        )
        assert uncertain is True


class TestSectionsComeFromTheDocumentNotTheModel:
    """Honouring the model's claim would let an untrusted artifact clear a safety Blocker
    by asserting the section exists."""

    def test_a_heading_in_the_document_counts(self, resolver: ModeResolver) -> None:
        assert resolver.sections_present(ARTIFACT, classify()) == {"safety"}

    def test_a_claimed_section_with_no_heading_does_not_count(self, resolver: ModeResolver) -> None:
        text = "# Design\n\nNo safety heading anywhere.\n"
        assert resolver.sections_present(text, classify(sections_present=["safety"])) == set()

    def test_the_unsupported_claim_is_logged(
        self, resolver: ModeResolver, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Discarded silently, a fabricated claim is invisible to whoever reads the report."""
        text = "# Design\n\nNo safety heading anywhere.\n"
        with caplog.at_level(logging.WARNING, logger="creative_agent.harness.classification"):
            resolver.sections_present(text, classify(sections_present=["safety"]))
        assert any("claim_unsupported" in r.getMessage() for r in caplog.records)


class TestTheClassLookupIsTyped:
    """DEC-F24: a bare `next()` raises StopIteration, which becomes a RuntimeError inside a
    coroutine — not a CreativeAgentError, so the CLI reports exit 5 instead of exit 3."""

    def test_a_known_class_resolves(self, resolver: ModeResolver) -> None:
        assert resolver.class_rule("architecture_design").name == "architecture_design"

    def test_an_unknown_class_is_a_typed_output_error(self, resolver: ModeResolver) -> None:
        with pytest.raises(LLMOutputError, match="unknown artifact class"):
            resolver.class_rule("no-such-class")

    def test_the_typed_error_is_what_the_cli_handler_catches(self, resolver: ModeResolver) -> None:
        with pytest.raises(CreativeAgentError):
            resolver.class_rule("no-such-class")


class TestSquash:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  Two   words \n", "two words"), ("A\tB", "a b"), ("", ""), ("SHOUT", "shout")],
    )
    def test_whitespace_and_case_are_normalised(self, raw: str, expected: str) -> None:
        assert squash(raw) == expected
