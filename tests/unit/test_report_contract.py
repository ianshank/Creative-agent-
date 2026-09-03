"""The published report contract, asserted without reference to the golden files.

The goldens byte-compare the renderer's output, which makes them exact but not
load-bearing: `make goldens` regenerates them from the renderer itself, so deleting a
section heading or the unverified marker and re-running that target produces a suite that
is green about the wrong document. Every assertion here names the contract directly —
what a review report must contain for a reader to be able to falsify it — so the same
deletion fails here no matter how often the goldens are regenerated.

These tests build reports from `tests.factories` plus direct model construction and never
read `tests/goldens/`. They assert the renderer's behaviour only; the renderer itself is
not modified by anything in this file.
"""

from __future__ import annotations

import pytest

from creative_agent.harness.rendering import OutputRenderer
from creative_agent.models.findings import Severity
from creative_agent.models.output import ReviewReport, Verdict
from creative_agent.models.state import EscalationEvent
from creative_agent.models.sweeps import RowDisposition, ScopeItem
from creative_agent.models.verification import VerificationEntry
from tests.factories import make_finding, make_key

# Deliberately not the shipped marker text: the renderer must substitute the marker it was
# constructed with (the oracle's `protocol.unverified_marker`), so a hard-coded literal in
# the renderer would fail here instead of quietly overriding oracle data.
SENTINEL_MARKER = "[[unverified-marker-sentinel]]"

# Headings a report must always carry, whatever the review found. Each one is a promise to
# the reader: what was wrong, what stood up, what is still unknown, and what the claims
# were checked against. A report missing any of them is not falsifiable, which is the
# failure mode the whole contract exists to prevent.
ALWAYS_REQUIRED_SECTIONS = (
    "## Findings",
    "## What survives",
    "## Residual risks",
    "## Verification log",
)

# Headings that appear only when the report carries the corresponding content — asserted
# in both directions below, since a section that renders unconditionally (or never) is as
# broken as a missing one.
ESCALATION_SECTION = "## STOP — charter review triggered"
SCOPE_SECTION_PREFIX = "## Scope"
SWEEP_SECTION = "## Doctrine sweep"


def _render(report: ReviewReport, marker: str = SENTINEL_MARKER) -> str:
    return OutputRenderer(marker).render(report)


def minimal_report(**overrides: object) -> ReviewReport:
    """The smallest valid report: no findings, no escalation, no scope, no log."""
    defaults: dict[str, object] = {
        "artifact_id": "contract-doc",
        "oracle_id": "mini",
        "oracle_version": "1.0",
        "cycle": 1,
        "verdict": Verdict(
            mode="advisory",
            confidence="Certain",
            headline="No doctrinal findings.",
        ),
    }
    return ReviewReport(**{**defaults, **overrides})  # type: ignore[arg-type]


def populated_report() -> ReviewReport:
    """A report exercising every optional section at once."""
    return minimal_report(
        cycle=3,
        verdict=Verdict(
            mode="conformance",
            mode_uncertain=True,
            confidence="Likely",
            headline="The compute budget is asserted, not derived.",
        ),
        findings=[
            make_finding(
                finding_id="F1",
                severity=Severity.BLOCKER,
                original_severity=Severity.BLOCKER,
                summary="Hardware section transcribes vendor specs without a budget",
                key=make_key("D1", "vendor-specs-no-budget"),
            )
        ],
        row_dispositions=[RowDisposition(row_id="D1", verdict="miss")],
        what_survives=["The runtime-assurance fallback design"],
        residual_risks=["Plasticity is unmeasured in the physical regime"],
        scope_items=[
            ScopeItem(
                reference="companion safety analysis",
                supplied=False,
                treated_as_unverified=True,
            )
        ],
        verification_log=[
            VerificationEntry(
                assertion="Workshop paper could not be fetched",
                row_id="D2",
                confidence="Likely",
                status="unverified_flagged",
            )
        ],
        escalation=EscalationEvent(
            key=make_key("D1", "vendor-specs-no-budget"),
            cycles=[1, 2, 3],
            message="Major finding D1+vendor-specs-no-budget has recurred across cycles "
            "[1, 2, 3] with disposition still open.",
        ),
    )


class TestSectionsAlwaysPresent:
    """A heading deleted from the renderer must fail here, not be regenerated away."""

    @pytest.mark.parametrize("heading", ALWAYS_REQUIRED_SECTIONS)
    def test_a_populated_report_carries_every_required_section(self, heading: str) -> None:
        assert heading in _render(populated_report()), f"{heading} vanished from the report"

    @pytest.mark.parametrize("heading", ALWAYS_REQUIRED_SECTIONS)
    def test_an_empty_report_still_carries_every_required_section(self, heading: str) -> None:
        """The dangerous direction: a clean review must still show what it checked.

        Rendering the sections only when they have content would turn "nothing was
        verified" into a report that simply does not mention verification.
        """
        assert heading in _render(minimal_report())

    def test_an_empty_report_says_so_rather_than_omitting_the_content(self) -> None:
        """Silence and "nothing found" read identically unless the report says which."""
        rendered = _render(minimal_report())
        assert "No findings." in rendered
        assert "(none stated)" in rendered
        assert "(empty)" in rendered


class TestVerdictLine:
    """The verdict is the one line a reader acts on; its parts are the contract."""

    def test_verdict_line_carries_mode_confidence_and_headline(self) -> None:
        rendered = _render(populated_report())
        assert "**VERDICT**" in rendered
        assert "mode=conformance" in rendered
        assert "[Likely]" in rendered
        assert "The compute budget is asserted, not derived." in rendered

    def test_an_uncertain_mode_is_marked_on_the_verdict_line(self) -> None:
        """Mode selection is fail-closed; a reader must see when it was a guess."""
        assert "(mode uncertain)" in _render(populated_report())

    def test_a_certain_mode_is_not_marked_uncertain(self) -> None:
        """The failure direction: a marker that always renders carries no information."""
        assert "(mode uncertain)" not in _render(minimal_report())

    def test_the_header_names_the_artifact_oracle_and_contract_version(self) -> None:
        """Two reports of different artifacts, oracles, or contract versions are not
        comparable; the header is what makes the difference visible."""
        rendered = _render(populated_report())
        report = populated_report()
        assert f"# Review: {report.artifact_id} — cycle {report.cycle}" in rendered
        assert f"Oracle: {report.oracle_id} v{report.oracle_version}" in rendered
        assert f"contract v{report.contract_version}" in rendered


class TestConditionalSections:
    def test_escalation_renders_the_stop_block_with_its_message(self) -> None:
        """The escalation hands the decision to the owner. A report that dropped the STOP
        block would look like an ordinary cycle and the hand-off would never happen."""
        rendered = _render(populated_report())
        assert ESCALATION_SECTION in rendered
        assert "has recurred across cycles" in rendered

    def test_no_escalation_means_no_stop_block(self) -> None:
        """A STOP block on every report is a STOP block nobody reads."""
        assert ESCALATION_SECTION not in _render(minimal_report())

    def test_scope_items_render_with_their_supplied_status(self) -> None:
        """An unsupplied dependency is never assumed sound; the report has to say so."""
        rendered = _render(populated_report())
        assert SCOPE_SECTION_PREFIX in rendered
        assert "companion safety analysis" in rendered
        assert "NOT SUPPLIED — unverified" in rendered

    def test_no_scope_items_means_no_scope_section(self) -> None:
        assert SCOPE_SECTION_PREFIX not in _render(minimal_report())

    def test_row_dispositions_render_the_doctrine_sweep(self) -> None:
        """The sweep is what shows a row was considered rather than skipped."""
        assert SWEEP_SECTION in _render(populated_report())

    def test_no_row_dispositions_means_no_sweep_section(self) -> None:
        assert SWEEP_SECTION not in _render(minimal_report())


class TestUnverifiedMarker:
    """The marker is the report's honesty mechanism: it is how a reader tells a checked
    claim from an unchecked one. Deleting it leaves a report that looks fully verified."""

    def test_an_unverified_entry_renders_the_marker_the_renderer_was_given(self) -> None:
        entry = VerificationEntry(
            assertion="Workshop paper could not be fetched",
            row_id="D2",
            confidence="Likely",
            status="unverified_flagged",
        )
        rendered = _render(minimal_report(verification_log=[entry]))
        assert SENTINEL_MARKER in rendered

    def test_the_marker_comes_from_the_constructor_not_a_literal(self) -> None:
        """Rendering the same report under two markers must produce two documents.

        A marker hard-coded in the renderer would pass the test above while silently
        ignoring the oracle's configured `protocol.unverified_marker`.
        """
        entry = VerificationEntry(
            assertion="Workshop paper could not be fetched",
            row_id="D2",
            confidence="Likely",
            status="unverified_flagged",
        )
        report = minimal_report(verification_log=[entry])
        other_marker = "[[a-different-marker]]"
        assert other_marker in _render(report, marker=other_marker)
        assert SENTINEL_MARKER not in _render(report, marker=other_marker)

    def test_the_raw_status_is_replaced_rather_than_printed_alongside(self) -> None:
        """`unverified_flagged` is an internal enum value, not a reader-facing statement;
        leaving it in place is how the marker silently stops being the signal."""
        entry = VerificationEntry(
            assertion="Workshop paper could not be fetched",
            row_id="D2",
            confidence="Likely",
            status="unverified_flagged",
        )
        assert "unverified_flagged" not in _render(minimal_report(verification_log=[entry]))

    def test_a_verified_entry_does_not_carry_the_marker(self) -> None:
        """The failure direction: a marker on every row flags nothing."""
        entry = VerificationEntry(
            assertion="D1 licenses the domain-content check",
            row_id="D1",
            canonical_id="arxiv:2001.00001",
            source_url="https://arxiv.org/abs/2001.00001",
            confidence="Certain",
            fetched=True,
            status="verified",
        )
        rendered = _render(minimal_report(verification_log=[entry]))
        assert SENTINEL_MARKER not in rendered
        assert "verified" in rendered


class TestFindingsTableIsAuditable:
    def test_a_capped_finding_shows_the_severity_it_was_capped_from(self) -> None:
        """A cap that hides the original severity is indistinguishable from a soft review:
        the reader cannot tell a Minor finding from a Blocker the policy capped."""
        report = minimal_report(
            findings=[
                make_finding(
                    finding_id="F2",
                    severity=Severity.INFO,
                    original_severity=Severity.MAJOR,
                    cap_reason="advisory mode cap",
                    summary="No preference relation stated",
                    key=make_key("D2", "no-preference-relation"),
                )
            ]
        )
        rendered = _render(report)
        assert "Info (was Major)" in rendered

    def test_findings_are_ordered_by_severity_so_the_worst_is_read_first(self) -> None:
        report = minimal_report(
            findings=[
                make_finding(
                    finding_id="F-minor",
                    severity=Severity.MINOR,
                    original_severity=Severity.MINOR,
                    key=make_key("D2", "minor-defect"),
                ),
                make_finding(
                    finding_id="F-blocker",
                    severity=Severity.BLOCKER,
                    original_severity=Severity.BLOCKER,
                    key=make_key("D1", "blocking-defect"),
                ),
            ]
        )
        rendered = _render(report)
        assert rendered.index("F-blocker") < rendered.index("F-minor")


class TestModelSuppliedRefsCannotForgeARow:
    """`gate_refs` reached the findings table unescaped (adversarial review, H1).

    DEC-F16 claimed the renderer escapes every model-supplied field it emits. It did not:
    `refs = ", ".join([*doctrine_refs, *gate_refs])` was interpolated raw, and `gate_refs`
    is copied verbatim from the model's `CandidateFinding` — an unconstrained `list[str]`
    that `launder_prose` never touched. One entry closed the cell and opened a forged
    Blocker row in the published report: the exact defect that decision exists to close, in
    the exact table it names, surviving the fix that named it.
    """

    FORGED = "ok |\n| F99 | Blocker | FORGED ROW | D1 | none |"

    def _render(self, **finding_kwargs: object) -> str:
        finding = make_finding(finding_id="F1", summary="benign", **finding_kwargs)
        report = ReviewReport(
            artifact_id="a",
            cycle=1,
            oracle_id="mini",
            oracle_version="1.0",
            verdict=Verdict(mode="advisory", confidence="Certain", headline="h"),
            findings=[finding],
        )
        return OutputRenderer("[Unverified]").render(report)

    def test_a_forged_row_in_gate_refs_does_not_become_a_row(self) -> None:
        rendered = self._render(gate_refs=[self.FORGED])
        assert "| F99 | Blocker |" not in rendered
        assert rendered.count("| F1 |") == 1

    def test_a_forged_row_in_doctrine_refs_does_not_become_a_row(self) -> None:
        rendered = self._render(doctrine_refs=[self.FORGED])
        assert "| F99 | Blocker |" not in rendered

    def test_the_findings_table_keeps_exactly_one_data_row_per_finding(self) -> None:
        """Counting rows is the assertion that survives a change of escaping strategy."""
        rendered = self._render(gate_refs=[self.FORGED, "also | bad"])
        body = rendered.split("## Findings", 1)[1].split("## What survives", 1)[0]
        data_rows = [
            line
            for line in body.splitlines()
            if line.startswith("|") and not set(line) <= set("|- ")
        ]
        assert len(data_rows) == 2, data_rows  # header + one finding
