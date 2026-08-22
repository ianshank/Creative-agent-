"""SeverityPolicy: data-driven capping with multi-support blocker legitimacy (DEC-F3).

Pure functions over validated models — no I/O, no clock, nothing hidden. This module is
a mutation-testing target: a softened comparison here is the worst silent failure the
harness can have.
"""

from __future__ import annotations

from creative_agent.models.findings import Finding, Severity, SupportRef
from creative_agent.models.oracle import DOCTRINE_TIER_BASIS, OracleTable
from creative_agent.models.review import ReviewMode


class SeverityPolicy:
    """Applies an oracle's severity rules to assembled findings."""

    def __init__(self, oracle: OracleTable) -> None:
        self._oracle = oracle
        self._policy = oracle.severity_policy
        self._blocker_tiers = tuple(oracle.severity_policy.blocker_tiers)

    def _row_tier(self, row_id: str) -> str | None:
        try:
            return self._oracle.row(row_id).tier
        except KeyError:
            return None

    def _doctrine_supports(self, finding: Finding) -> list[SupportRef]:
        return [s for s in finding.supports if s.kind == "doctrine_row"]

    def _has_blocker_basis(self, finding: Finding) -> bool:
        allowed = set(self._policy.blocker_requires_any_of)
        for support in finding.supports:
            if support.kind == "doctrine_row":
                if (
                    DOCTRINE_TIER_BASIS in allowed
                    and self._row_tier(support.ref) in self._blocker_tiers
                ):
                    return True
            # Non-doctrine support kinds share their names with the basis vocabulary.
            elif support.kind in allowed:
                return True
        return False

    def _tier_cap(self, finding: Finding) -> tuple[Severity, str] | None:
        """The spec's "alone" semantics: a cap binds only when every support falls
        inside the capped tiers and nothing else legitimizes the finding."""
        doctrine = self._doctrine_supports(finding)
        if not doctrine:
            return None
        non_doctrine = [s for s in finding.supports if s.kind != "doctrine_row"]
        if non_doctrine:
            return None
        for cap in self._policy.tier_caps:
            capped_tiers = set(cap.tiers)
            tiers = {self._row_tier(s.ref) for s in doctrine}
            if tiers and tiers <= capped_tiers:
                return Severity.parse(cap.max_solo_severity), cap.reason
        return None

    def _staleness_cap(self, finding: Finding) -> tuple[Severity, str] | None:
        doctrine = self._doctrine_supports(finding)
        if not doctrine:
            return None
        rows = []
        for support in doctrine:
            try:
                rows.append(self._oracle.row(support.ref))
            except KeyError:
                return None
        if rows and all(row.is_stale(self._oracle.freshness) for row in rows):
            cap = Severity.parse(self._policy.unverified_row_cap)
            return cap, (
                "all supporting rows are unverified beyond the re-baseline budget "
                "(oracle freshness rule)"
            )
        return None

    def cap(self, finding: Finding, mode: ReviewMode) -> Finding:
        """Return the finding with its effective severity; never raises severity."""
        severity = Severity.parse(finding.original_severity)
        reasons: list[str] = []

        if mode == "advisory":
            advisory_cap = Severity.parse(self._oracle.conformance.advisory_severity_cap)
            if severity > advisory_cap:
                severity = advisory_cap
                reasons.append(
                    "advisory mode: artifact does not claim conformance; findings are "
                    "capped (non-conformance with a program the author never joined is "
                    "not a defect)"
                )

        stale = self._staleness_cap(finding)
        if stale is not None and severity > stale[0]:
            severity = stale[0]
            reasons.append(stale[1])

        tier = self._tier_cap(finding)
        if tier is not None and severity > tier[0]:
            severity = tier[0]
            reasons.append(tier[1])

        if severity is Severity.BLOCKER and not self._has_blocker_basis(finding):
            severity = Severity.MAJOR
            bases = ", ".join(self._policy.blocker_requires_any_of)
            tiers = "/".join(self._blocker_tiers)
            reasons.append(
                f"blocker requires one of [{bases}] among its supports "
                f"(doctrine rows qualify at tier {tiers})"
            )

        return finding.model_copy(
            update={
                "severity": severity,
                "cap_reason": "; ".join(reasons) if reasons else None,
            }
        )

    def cap_all(self, findings: list[Finding], mode: ReviewMode) -> list[Finding]:
        return [self.cap(f, mode) for f in findings]
