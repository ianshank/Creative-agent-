"""Golden-file contract tests: report rendering and assembled prompts, byte-compared.

Update with `uv run pytest --update-goldens` (and say so in the PR — see CLAUDE.md).
A golden-hygiene test guards against environment leaking into the files.
"""

from __future__ import annotations

import re

import pytest

from creative_agent.harness.llm.base import CallKind
from creative_agent.harness.prompting import PromptAssembler, render_oracle_rows
from creative_agent.harness.rendering import OutputRenderer
from creative_agent.models.findings import Severity, SupportRef
from creative_agent.models.output import ReviewReport, Verdict
from creative_agent.models.state import EscalationEvent
from creative_agent.models.sweeps import RowDisposition, ScopeItem
from creative_agent.models.verification import VerificationEntry
from tests.conftest import GOLDEN_DIR, GoldenComparer
from tests.factories import make_finding, make_key, make_oracle

MARKER = "[Unverified — flagged for human check]"

# Environment that must never reach a golden file: an absolute path from whoever ran
# `--update-goldens`, or a wall-clock timestamp. Both make the goldens unreproducible on
# anyone else's machine.
#
# The Windows alternative is `[A-Z]:` followed by ONE literal backslash. Written as
# `[A-Z]:\\\\` inside a raw string it compiled to two consecutive backslashes, which no
# Windows path contains — a dead branch that could never match, in a check whose whole job
# is to match. `TestGoldenHygiene.test_the_leak_pattern_matches_real_leaks` now holds the
# pattern to its claim.
ENVIRONMENT_LEAK = re.compile(r"(/home/|/root/|/tmp/|[A-Z]:\\|\d{4}-\d{2}-\d{2}T\d{2}:)")


def full_report() -> ReviewReport:
    return ReviewReport(
        artifact_id="rl-blueprint",
        oracle_id="mini",
        oracle_version="1.0",
        cycle=3,
        verdict=Verdict(
            mode="conformance",
            confidence="Likely",
            headline="The compute budget is asserted, not derived.",
        ),
        findings=[
            make_finding(
                finding_id="F1",
                severity=Severity.BLOCKER,
                original_severity=Severity.BLOCKER,
                summary="Hardware section transcribes vendor specs without a budget",
                gate_refs=["compute_budget"],
                supports=[SupportRef(kind="gate_failure", ref="compute_budget")],
                disposition_required="State FLOPs per control step on the named SoC",
                key=make_key("D1", "vendor-specs-no-budget"),
            ),
            make_finding(
                finding_id="F2",
                severity=Severity.INFO,
                original_severity=Severity.MAJOR,
                cap_reason="advisory mode cap",
                summary="No preference relation stated",
                key=make_key("D2", "no-preference-relation"),
            ),
        ],
        row_dispositions=[
            RowDisposition(row_id="D1", verdict="miss"),
            RowDisposition(
                row_id="D2", verdict="not_applicable", na_reason="no reward claims made"
            ),
        ],
        what_survives=["The runtime-assurance fallback design"],
        residual_risks=["Plasticity is unmeasured in the physical regime"],
        scope_items=[
            ScopeItem(
                reference="companion safety analysis", supplied=False, treated_as_unverified=True
            )
        ],
        verification_log=[
            VerificationEntry(
                assertion="D1 licenses the domain-content check",
                row_id="D1",
                canonical_id="arxiv:2001.00001",
                source_url="https://arxiv.org/abs/2001.00001",
                confidence="Certain",
                fetched=True,
                status="verified",
            ),
            VerificationEntry(
                assertion="Workshop paper could not be fetched",
                row_id="D2",
                confidence="Likely",
                status="unverified_flagged",
            ),
        ],
        escalation=EscalationEvent(
            key=make_key("D1", "vendor-specs-no-budget"),
            cycles=[1, 2, 3],
            message="Major finding D1+vendor-specs-no-budget has recurred across cycles "
            "[1, 2, 3] with disposition still open. STOP: charter review triggered — the "
            "decision passes to the owner.",
        ),
    )


class TestReportGoldens:
    def test_full_report(self, golden: GoldenComparer) -> None:
        rendered = OutputRenderer(MARKER).render(full_report())
        golden.check("report-full.md", rendered)

    def test_empty_report(self, golden: GoldenComparer) -> None:
        report = ReviewReport(
            artifact_id="clean-doc",
            oracle_id="mini",
            oracle_version="1.0",
            cycle=1,
            verdict=Verdict(
                mode="advisory",
                mode_uncertain=True,
                confidence="Certain",
                headline="No doctrinal findings.",
            ),
        )
        golden.check("report-empty.md", OutputRenderer(MARKER).render(report))


class TestPromptGoldens:
    """A template edit that drops a hard rule must fail CI."""

    def test_system_and_row_prompts(self, golden: GoldenComparer) -> None:
        oracle = make_oracle()
        assembler = PromptAssembler([], "default")
        prompt = assembler.assemble(
            CallKind.ROW,
            ref="D1",
            allowed_tools=["Read", "WebFetch"],
            fetch_domain_allowlist=["arxiv.org"],
            context={
                "oracle": oracle,
                "artifact": "<<<ARTIFACT-UNDER-REVIEW\n(example)\nEND-ARTIFACT>>>",
                "fetch_domains": ["arxiv.org", "doi.org"],
                "prior_keys": ["D1+some-defect"],
                "repair_defects": [],
                "mode": "conformance",
                "artifact_class": "architecture_design",
                "row": oracle.rows[0],
                "row_rendered": render_oracle_rows([oracle.rows[0]]),
            },
        )
        golden.check("prompt-system.md", prompt.system)
        golden.check("prompt-row-D1.md", prompt.user)


class TestGoldenHygiene:
    def test_no_environment_leaks_into_goldens(self) -> None:
        assert GOLDEN_DIR.is_dir(), "goldens not generated — run pytest --update-goldens"
        for path in GOLDEN_DIR.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            match = ENVIRONMENT_LEAK.search(text)
            assert match is None, f"{path.name} leaks environment: {match.group(0)!r}"

    @pytest.mark.parametrize(
        "leak",
        [
            "/home/someone/repo/docs/design.md",
            "/root/repo/docs/design.md",
            "/tmp/pytest-of-runner/artifact.md",
            r"C:\Users\someone\repo\docs\design.md",
            "2026-01-01T09:30:00Z",
        ],
    )
    def test_the_leak_pattern_matches_real_leaks(self, leak: str) -> None:
        """The scan above only proves something if every alternative can actually fire.

        The Windows branch could not: it required two consecutive backslashes. A goldens
        file regenerated on Windows would have passed this hygiene check while carrying an
        absolute path, and nothing would have said so.
        """
        assert ENVIRONMENT_LEAK.search(leak), f"the hygiene scan cannot see {leak!r}"

    def test_the_leak_pattern_does_not_match_ordinary_report_text(self) -> None:
        """The other direction: a pattern that matches everything would make the goldens
        unwritable and get deleted rather than fixed."""
        for benign in ("docs/design.md", "arxiv:2001.00001", "cycle 3", "2026-01-01"):
            assert ENVIRONMENT_LEAK.search(benign) is None, f"{benign!r} is not a leak"
