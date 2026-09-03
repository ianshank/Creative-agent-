"""OutputRenderer: the published report contract (golden-tested, byte-stable).

Deterministic by construction: findings sorted (severity desc, then key), no wall-clock
reads, repo-relative paths only, LF newlines. LLM prose reaches this module already
laundered (ThreatGuard).
"""

from __future__ import annotations

from creative_agent.models.findings import Finding, Severity
from creative_agent.models.output import ReviewReport
from creative_agent.models.verification import VerificationEntry


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-int(f.severity), f.key.render(), f.finding_id))


def _md_escape(text: str) -> str:
    """Neutralise the two characters that let a value escape its cell or its line.

    `\\r` is here because most markdown renderers break a line on a bare carriage return,
    so escaping only `\\n` left `"clean\\r| forged | row |"` able to add a table row.
    ThreatGuard.launder_prose already folds both, but this is the renderer's own
    guarantee and must not depend on an upstream caller having laundered correctly.
    """
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _bullet(text: str) -> str:
    """A list item built from model-supplied prose.

    Bullets were previously interpolated raw, so a newline in `what_survives` or
    `residual_risks` ended the list and let the rest of the value render as top-level
    markdown — a forged `## Findings` section among other things (DEC-F16).
    """
    return f"- {_md_escape(text)}"


class OutputRenderer:
    """Renders a ReviewReport to the markdown contract."""

    def __init__(self, unverified_marker: str) -> None:
        self._unverified_marker = unverified_marker

    def _verification_row(self, entry: VerificationEntry) -> str:
        source = entry.canonical_id or entry.source_url or "—"
        status: str = entry.status
        if status == "unverified_flagged":
            status = self._unverified_marker
        return (
            f"| {_md_escape(entry.assertion)} | {_md_escape(entry.row_id or '—')} | "
            f"{_md_escape(source)} | {entry.confidence} | "
            f"{'fetched' if entry.fetched else 'not fetched'} | {_md_escape(status)} |"
        )

    def render(self, report: ReviewReport) -> str:
        lines: list[str] = []
        verdict = report.verdict
        mode_bit = f"mode={verdict.mode}"
        if verdict.mode_uncertain:
            mode_bit += " (mode uncertain)"
        lines.append(f"# Review: {report.artifact_id} — cycle {report.cycle}")
        lines.append("")
        lines.append(
            f"Oracle: {report.oracle_id} v{report.oracle_version} "
            f"(contract v{report.contract_version})"
        )
        lines.append("")
        lines.append(
            f"**VERDICT** [{mode_bit}] [{verdict.confidence}]: {_md_escape(verdict.headline)}"
        )
        lines.append("")

        if report.escalation is not None:
            lines.append("## STOP — charter review triggered")
            lines.append("")
            lines.append(_md_escape(report.escalation.message))
            lines.append("")

        lines.append("## Findings")
        lines.append("")
        if report.findings:
            lines.append("| ID | Severity | Finding | Doctrine/gate | Required disposition |")
            lines.append("|---|---|---|---|---|")
            for finding in _sorted_findings(report.findings):
                refs = ", ".join([*finding.doctrine_refs, *finding.gate_refs]) or "—"
                severity = Severity.parse(finding.severity).name.title()
                if finding.cap_reason:
                    severity += f" (was {Severity.parse(finding.original_severity).name.title()})"
                lines.append(
                    f"| {finding.finding_id} | {severity} | {_md_escape(finding.summary)} | "
                    f"{refs} | {_md_escape(finding.disposition_required) or '—'} |"
                )
        else:
            lines.append("No findings.")
        lines.append("")

        lines.append("## What survives")
        lines.append("")
        lines.extend([_bullet(item) for item in report.what_survives] or ["- (none stated)"])
        lines.append("")
        lines.append("## Residual risks")
        lines.append("")
        lines.extend([_bullet(item) for item in report.residual_risks] or ["- (none stated)"])
        lines.append("")

        if report.row_dispositions:
            lines.append("## Doctrine sweep")
            lines.append("")
            lines.append("| Row | Verdict | N/A reason |")
            lines.append("|---|---|---|")
            for disposition in report.row_dispositions:
                lines.append(
                    f"| {disposition.row_id} | {disposition.verdict} | "
                    f"{_md_escape(disposition.na_reason or '—')} |"
                )
            lines.append("")

        if report.scope_items:
            lines.append("## Scope (unsupplied dependencies — unverified, never assumed sound)")
            lines.append("")
            for item in report.scope_items:
                status = "supplied" if item.supplied else "NOT SUPPLIED — unverified"
                lines.append(f"- {_md_escape(item.reference)}: {status}")
            lines.append("")

        lines.append("## Verification log")
        lines.append("")
        if report.verification_log:
            lines.append("| Assertion | Row | Source | Confidence | Fetch | Status |")
            lines.append("|---|---|---|---|---|---|")
            lines.extend(self._verification_row(e) for e in report.verification_log)
        else:
            lines.append("(empty)")
        lines.append("")
        return "\n".join(lines)
