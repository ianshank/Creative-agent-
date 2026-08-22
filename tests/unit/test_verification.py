"""VerificationLogChecker: completeness, tool honesty, attribution sweep."""

from creative_agent.harness.llm.base import ToolEvidence
from creative_agent.harness.verification import VerificationLogChecker
from tests.factories import make_finding, make_verification

AUTHORS = {"Richard S. Sutton", "Will Dabney", "J. Fernando Hernandez-Garcia"}


def checker() -> VerificationLogChecker:
    return VerificationLogChecker(author_names=AUTHORS)


def fetch(target: str, tool: str = "WebFetch", ok: bool = True) -> ToolEvidence:
    return ToolEvidence(tool_name=tool, target=target, ok=ok)


class TestCompleteness:
    def test_finding_without_matching_entry_is_defect(self) -> None:
        defects = checker().check_completeness(
            [make_finding(doctrine_refs=["D2"])], [make_verification(row_id="D1")]
        )
        assert len(defects) == 1 and "D2" in defects[0]

    def test_complete_log_passes(self) -> None:
        defects = checker().check_completeness(
            [make_finding(doctrine_refs=["D1"])], [make_verification(row_id="D1")]
        )
        assert defects == []


class TestToolHonesty:
    def test_fetched_true_with_matching_result_passes(self) -> None:
        entries = [make_verification(canonical_id="arxiv:2001.00001")]
        evidence = [fetch("https://arxiv.org/abs/2001.00001v3")]
        assert checker().check_tool_honesty(entries, evidence) == []

    def test_fetched_true_without_result_is_defect(self) -> None:
        entries = [make_verification()]
        assert len(checker().check_tool_honesty(entries, [])) == 1

    def test_failed_fetch_does_not_count(self) -> None:
        entries = [make_verification(canonical_id="arxiv:2001.00001")]
        evidence = [fetch("https://arxiv.org/abs/2001.00001", ok=False)]
        assert len(checker().check_tool_honesty(entries, evidence)) == 1

    def test_websearch_never_counts_as_fetched(self) -> None:
        entries = [make_verification(canonical_id="arxiv:2001.00001")]
        evidence = [fetch("https://arxiv.org/abs/2001.00001", tool="WebSearch")]
        assert len(checker().check_tool_honesty(entries, evidence)) == 1

    def test_unfetched_entries_are_not_checked(self) -> None:
        entries = [
            make_verification(fetched=False, status="unverified_flagged", confidence="Likely")
        ]
        assert checker().check_tool_honesty(entries, []) == []

    def test_read_of_local_artifact_counts_for_exact_target(self) -> None:
        entries = [make_verification(source_url="docs/design.md", canonical_id=None)]
        evidence = [fetch("docs/design.md", tool="Read")]
        assert checker().check_tool_honesty(entries, evidence) == []


class TestAttributionSweep:
    def test_impersonation_phrase_is_defect(self) -> None:
        defects = checker().check_attribution(["Sutton would say this design is wrong."])
        assert len(defects) == 1 and "Sutton" in defects[0]

    def test_believes_phrase_is_defect(self) -> None:
        assert checker().check_attribution(["Dabney believes the axioms fail here."])

    def test_diacritics_do_not_evade_the_sweep(self) -> None:
        assert checker().check_attribution(["Hernández-García would say otherwise."])

    def test_cited_attribution_with_year_passes(self) -> None:
        text = "According to Sutton (2019), general methods win."
        assert checker().check_attribution([text]) == []

    def test_neutral_mention_passes(self) -> None:
        text = "The Sutton corpus defines reward-respecting subtasks precisely."
        assert checker().check_attribution([text]) == []


def test_check_aggregates_all_defect_classes() -> None:
    defects = checker().check(
        findings=[make_finding(doctrine_refs=["D9"])],
        entries=[make_verification()],  # row D1, fetched with no evidence
        evidence=[],
        prose_blocks=["Sutton believes this is fine."],
    )
    assert len(defects) == 3
