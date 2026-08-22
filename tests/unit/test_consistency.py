"""Internal-consistency sweep: duplicate symbol definitions are Blocker candidates."""

from creative_agent.harness.consistency import ConsistencyChecker, extract_definitions
from creative_agent.models.findings import Severity


class TestExtraction:
    def test_assignment_definitions_extracted(self) -> None:
        text = "gamma := 0.99\nalpha_t = eta / norm(g)\n"
        defs = extract_definitions(text)
        assert defs["gamma"] == ["0.99"]
        assert defs["alpha_t"] == ["eta / norm(g)"]

    def test_verbal_definitions_extracted(self) -> None:
        defs = extract_definitions("Let tau be the control horizon in seconds.")
        assert defs["tau"] == ["the control horizon in seconds"]

    def test_code_fences_ignored(self) -> None:
        text = "```python\nx = 1\nx = 2\n```\n"
        assert extract_definitions(text) == {}

    def test_unterminated_fence_does_not_blind_the_document(self) -> None:
        """One stray fence must not disable the Blocker sweep for the whole tail."""
        text = "```\nsnippet\n\ngamma = 0.99\n\ngamma = 0.5\n"
        assert extract_definitions(text)["gamma"] == ["0.99", "0.5"]

    def test_configurable_prose_symbols(self) -> None:
        assert extract_definitions("Nota = importante", prose_symbols=("nota",)) == {}

    def test_prose_words_ignored(self) -> None:
        assert extract_definitions("Note = this is important") == {}

    def test_identical_redefinition_collapses(self) -> None:
        defs = extract_definitions("gamma = 0.99\ngamma = 0.99\n")
        assert defs["gamma"] == ["0.99"]


class TestChecker:
    def test_conflicting_definitions_are_blocker(self) -> None:
        text = "gamma = 0.99\n...\ngamma = 0.95\n"
        findings = ConsistencyChecker().check(text)
        assert len(findings) == 1
        assert findings[0].severity is Severity.BLOCKER
        assert findings[0].supports[0].kind == "internal_contradiction"
        assert "gamma" in findings[0].summary

    def test_consistent_document_passes(self) -> None:
        assert ConsistencyChecker().check("gamma = 0.99\nalpha = 0.1\n") == []
