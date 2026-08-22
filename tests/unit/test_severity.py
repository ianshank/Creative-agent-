"""SeverityPolicy matrix: {tier} x {mode} x {stale} x {supports} + properties."""

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from creative_agent.harness.severity import SeverityPolicy
from creative_agent.models.findings import Severity, SupportRef
from creative_agent.models.oracle import FreshnessMeta, OracleTable, SeverityPolicyConfig
from creative_agent.models.review import ReviewMode
from tests.factories import make_finding, make_key, make_oracle, make_row, make_source

STALE_FRESHNESS = FreshnessMeta(
    last_rebaselined=date(2026, 1, 1),
    rebaseline_count=2,
    max_rebaselines_without_verification=2,
)


def oracle_with(rows: list, **overrides: object) -> OracleTable:
    return make_oracle(rows=rows, **overrides)


def doctrine(ref: str) -> SupportRef:
    return SupportRef(kind="doctrine_row", ref=ref)


class TestAdvisoryCap:
    def test_blocker_capped_to_info_in_advisory(self) -> None:
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(severity=Severity.BLOCKER, original_severity=Severity.BLOCKER)
        capped = policy.cap(finding, "advisory")
        assert capped.severity is Severity.INFO
        assert capped.original_severity is Severity.BLOCKER
        assert capped.cap_reason and "advisory" in capped.cap_reason

    def test_info_untouched_in_advisory(self) -> None:
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(severity=Severity.INFO, original_severity=Severity.INFO)
        assert policy.cap(finding, "advisory").cap_reason is None


class TestTierCapAloneSemantics:
    """The spec's 'alone': T/E-only support caps at Major; corroboration lifts the cap."""

    def _te_oracle(self) -> OracleTable:
        return oracle_with(
            [
                make_row("D1", tier="E", sources=[make_source(tier="E")]),
                make_row("D2", tier="PR"),
            ]
        )

    def test_te_only_blocker_capped_to_major(self) -> None:
        policy = SeverityPolicy(self._te_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=["D1"],
            supports=[doctrine("D1")],
        )
        capped = policy.cap(finding, "conformance")
        assert capped.severity is Severity.MAJOR
        assert capped.cap_reason

    def test_te_plus_pr_row_keeps_blocker(self) -> None:
        policy = SeverityPolicy(self._te_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=["D1", "D2"],
            supports=[doctrine("D1"), doctrine("D2")],
        )
        assert policy.cap(finding, "conformance").severity is Severity.BLOCKER

    def test_te_plus_gate_failure_keeps_blocker(self) -> None:
        policy = SeverityPolicy(self._te_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=["D1"],
            supports=[doctrine("D1"), SupportRef(kind="gate_failure", ref="compute_budget")],
        )
        assert policy.cap(finding, "conformance").severity is Severity.BLOCKER

    def test_te_major_not_touched_by_tier_cap(self) -> None:
        policy = SeverityPolicy(self._te_oracle())
        finding = make_finding(
            severity=Severity.MAJOR,
            original_severity=Severity.MAJOR,
            doctrine_refs=["D1"],
            supports=[doctrine("D1")],
        )
        assert policy.cap(finding, "conformance").severity is Severity.MAJOR


class TestBlockerLegitimacy:
    def test_blocker_without_basis_downgraded(self) -> None:
        # PR row exists in the oracle but the finding cites no support at all.
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=[],
            supports=[],
        )
        capped = policy.cap(finding, "conformance")
        assert capped.severity is Severity.MAJOR
        assert capped.cap_reason and "blocker requires" in capped.cap_reason

    @pytest.mark.parametrize(
        "support",
        [
            SupportRef(kind="safety_failure", ref="safety"),
            SupportRef(kind="internal_contradiction", ref="gamma"),
            SupportRef(kind="gate_failure", ref="compute_budget"),
        ],
    )
    def test_non_doctrine_bases_legitimize_blocker(self, support: SupportRef) -> None:
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=[],
            supports=[support],
        )
        assert policy.cap(finding, "conformance").severity is Severity.BLOCKER


class TestStalenessCap:
    def _stale_oracle(self) -> OracleTable:
        return oracle_with(
            [make_row("D1", sources=[make_source(verified=False)]), make_row("D2")],
            freshness=STALE_FRESHNESS,
        )

    def test_all_stale_rows_capped_to_minor(self) -> None:
        policy = SeverityPolicy(self._stale_oracle())
        finding = make_finding(
            severity=Severity.MAJOR,
            original_severity=Severity.MAJOR,
            doctrine_refs=["D1"],
            supports=[doctrine("D1")],
        )
        capped = policy.cap(finding, "conformance")
        assert capped.severity is Severity.MINOR
        assert capped.cap_reason and "re-baseline" in capped.cap_reason

    def test_one_fresh_row_avoids_staleness_cap(self) -> None:
        policy = SeverityPolicy(self._stale_oracle())
        finding = make_finding(
            severity=Severity.MAJOR,
            original_severity=Severity.MAJOR,
            doctrine_refs=["D1", "D2"],
            supports=[doctrine("D1"), doctrine("D2")],
        )
        assert policy.cap(finding, "conformance").severity is Severity.MAJOR


_SEVERITIES = st.sampled_from(list(Severity))
_MODES = st.sampled_from(["conformance", "advisory"])
_SUPPORT_SETS = st.lists(
    st.sampled_from(
        [
            SupportRef(kind="doctrine_row", ref="D1"),
            SupportRef(kind="doctrine_row", ref="D2"),
            SupportRef(kind="gate_failure", ref="observable"),
            SupportRef(kind="safety_failure", ref="safety"),
            SupportRef(kind="internal_contradiction", ref="x"),
        ]
    ),
    max_size=4,
)


class TestProperties:
    @given(severity=_SEVERITIES, mode=_MODES, supports=_SUPPORT_SETS)
    def test_cap_never_raises_severity_and_is_idempotent(
        self, severity: Severity, mode: ReviewMode, supports: list[SupportRef]
    ) -> None:
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(
            severity=severity,
            original_severity=severity,
            doctrine_refs=[s.ref for s in supports if s.kind == "doctrine_row"],
            supports=supports,
            key=make_key(),
        )
        once = policy.cap(finding, mode)
        assert once.severity <= severity
        assert once.original_severity is severity
        twice = policy.cap(once, mode)
        assert twice.severity is once.severity


class TestUnknownAndEmptySupports:
    """SeverityPolicy is a public seam; it must fail closed on inputs the pipeline
    would normally filter out."""

    def test_unknown_row_in_supports_does_not_escape_the_staleness_cap(self) -> None:
        policy = SeverityPolicy(
            oracle_with(
                [make_row("D1", sources=[make_source(verified=False)]), make_row("D2")],
                freshness=STALE_FRESHNESS,
            )
        )
        finding = make_finding(
            severity=Severity.MAJOR,
            original_severity=Severity.MAJOR,
            doctrine_refs=["D1", "DZ9"],
            supports=[doctrine("D1"), doctrine("DZ9")],
        )
        # An unknown ref must not become a laundering route around the cap.
        assert policy.cap(finding, "conformance").severity is Severity.MINOR

    def test_unknown_row_alone_cannot_justify_a_blocker(self) -> None:
        policy = SeverityPolicy(make_oracle())
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=["DZ9"],
            supports=[doctrine("DZ9")],
        )
        assert policy.cap(finding, "conformance").severity is Severity.MAJOR

    def test_restrictive_blocker_policy_downgrades_gate_only_findings(self) -> None:
        """blocker_requires_any_of is data: a narrower list must actually bind."""
        oracle = make_oracle(
            severity_policy=SeverityPolicyConfig(
                unverified_row_cap=Severity.MINOR,
                blocker_requires_any_of=["tier_pr_or_ap_row"],
            )
        )
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=[],
            supports=[SupportRef(kind="gate_failure", ref="observable")],
        )
        assert SeverityPolicy(oracle).cap(finding, "conformance").severity is Severity.MAJOR

    def test_blocker_tiers_are_configurable(self) -> None:
        """An oracle can require peer review specifically, not PR-or-AP."""
        oracle = make_oracle(
            rows=[make_row("D1", tier="AP", sources=[make_source(tier="AP")])],
            severity_policy=SeverityPolicyConfig(
                unverified_row_cap=Severity.MINOR,
                blocker_requires_any_of=["tier_pr_or_ap_row"],
                blocker_tiers=["PR"],
            ),
        )
        finding = make_finding(
            severity=Severity.BLOCKER,
            original_severity=Severity.BLOCKER,
            doctrine_refs=["D1"],
            supports=[doctrine("D1")],
        )
        assert SeverityPolicy(oracle).cap(finding, "conformance").severity is Severity.MAJOR
