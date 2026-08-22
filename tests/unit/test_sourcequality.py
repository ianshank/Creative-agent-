"""Deterministic source-quality checks."""

from creative_agent.harness.sourcequality import SourceQualityChecker
from creative_agent.models.findings import Severity
from tests.factories import make_oracle


def checker() -> SourceQualityChecker:
    return SourceQualityChecker(make_oracle().source_quality)


class TestClusterCitations:
    def test_cluster_at_threshold_flagged_as_major(self) -> None:
        text = "This paragraph asserts a lot. [1][2][3][4]"
        findings = checker().cluster_citations(text)
        assert len(findings) == 1
        assert findings[0].severity is Severity.MAJOR
        assert "[1][2][3][4]" in findings[0].summary

    def test_below_threshold_not_flagged(self) -> None:
        assert checker().cluster_citations("Well-mapped claim. [1][2]") == []

    def test_spaced_clusters_still_detected(self) -> None:
        assert len(checker().cluster_citations("claims [1] [2] [3] [4] here")) == 1


class TestBibliographyHygiene:
    def test_same_paper_under_two_urls_is_minor(self) -> None:
        text = "- https://arxiv.org/abs/2208.11173\n- https://arxiv.org/pdf/2208.11173v2\n"
        findings = checker().bibliography_hygiene(text)
        assert len(findings) == 1
        assert findings[0].severity is Severity.MINOR
        assert "arxiv:2208.11173" in findings[0].summary

    def test_distinct_papers_pass(self) -> None:
        text = "https://arxiv.org/abs/2208.11173 and https://arxiv.org/abs/2202.03466"
        assert checker().bibliography_hygiene(text) == []


class TestVendorPages:
    def test_vendor_url_detected_including_subdomains(self) -> None:
        text = "Per https://docs.vendor.example/chip and https://other.example/x"
        assert checker().vendor_urls(text) == ["https://docs.vendor.example/chip"]

    def test_vendor_finding_is_info_with_note(self) -> None:
        findings = checker().vendor_findings("see https://vendor.example/specs")
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO
        assert "not evidence" in findings[0].summary

    def test_no_vendor_urls_no_findings(self) -> None:
        assert checker().vendor_findings("no urls at all") == []


def test_check_combines_all_checks() -> None:
    text = (
        "Claims. [1][2][3][4]\n"
        "https://arxiv.org/abs/2208.11173 https://arxiv.org/pdf/2208.11173\n"
        "https://vendor.example/perf\n"
    )
    findings = checker().check(text)
    kinds = {f.anchor.split("-")[0] for f in findings}
    assert kinds == {"cluster", "duplicate", "vendor"}
