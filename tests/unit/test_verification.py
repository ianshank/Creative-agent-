"""VerificationLogChecker: completeness, tool honesty, attribution sweep."""

from creative_agent.harness.llm.base import ToolEvidence
from creative_agent.harness.verification import VerificationLogChecker
from creative_agent.models.verification import VerificationEntry
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


class TestEvidenceIsAuthorityBound:
    """A fetch may only vouch for an identifier its own registrar served (DEC-F12).

    `_evidence_identifiers` canonicalized the *requested target string*, and
    `canonicalize` matches an arXiv id or DOI as a substring anywhere in that string. Three
    different targets therefore credited `fetched=True` for the same paper: the genuine
    URL, a decoy on any host the artifact's own bibliography put on the allowlist, and a
    local `Read` of a file whose name embedded the identifier — the artifact repository is
    a read root and its author controls the file names, so that variant needed no network
    at all. `docs/architecture.md` called this "the control that makes the verification log
    worth reading".
    """

    PAPER = "2401.12345"

    def _checker(self) -> VerificationLogChecker:
        return VerificationLogChecker(author_names=set())

    def _entry(self, **overrides: object) -> VerificationEntry:
        defaults: dict[str, object] = {
            "assertion": "the paper says so",
            "row_id": "D1",
            "canonical_id": f"arXiv:{self.PAPER}",
            "source_url": f"https://arxiv.org/abs/{self.PAPER}",
            "confidence": "Certain",
            "fetched": True,
            "status": "verified",
        }
        return VerificationEntry(**{**defaults, **overrides})  # type: ignore[arg-type]

    def test_a_genuine_fetch_from_the_registrar_is_credited(self) -> None:
        evidence = [
            ToolEvidence(
                tool_name="WebFetch", target=f"https://arxiv.org/abs/{self.PAPER}", ok=True
            )
        ]
        assert self._checker().check_tool_honesty([self._entry()], evidence) == []

    def test_a_subdomain_of_the_registrar_is_credited(self) -> None:
        """export.arxiv.org is the host the arXiv API actually serves from."""
        evidence = [
            ToolEvidence(
                tool_name="WebFetch",
                target=f"https://export.arxiv.org/abs/{self.PAPER}",
                ok=True,
            )
        ]
        assert self._checker().check_tool_honesty([self._entry()], evidence) == []

    def test_a_decoy_url_on_an_allowlisted_host_is_refused(self) -> None:
        """The artifact's own bibliography puts arbitrary public hosts on the allowlist."""
        evidence = [
            ToolEvidence(
                tool_name="WebFetch",
                target=f"https://attacker.example/x?src=arxiv.org/abs/{self.PAPER}",
                ok=True,
            )
        ]
        defects = self._checker().check_tool_honesty([self._entry()], evidence)
        assert len(defects) == 1
        assert "from its own registrar" in defects[0]

    def test_a_local_read_of_a_cleverly_named_file_is_refused(self) -> None:
        """Needs no network: the artifact repo is a read root and its author names files."""
        evidence = [
            ToolEvidence(
                tool_name="Read", target=f"/repo/refs/arxiv.org/abs/{self.PAPER}.md", ok=True
            )
        ]
        defects = self._checker().check_tool_honesty([self._entry()], evidence)
        assert len(defects) == 1

    def test_a_lookalike_domain_is_refused(self) -> None:
        """Suffix matching is anchored on a dot, so notarxiv.org is not arxiv.org."""
        evidence = [
            ToolEvidence(
                tool_name="WebFetch", target=f"https://notarxiv.org/abs/{self.PAPER}", ok=True
            )
        ]
        assert len(self._checker().check_tool_honesty([self._entry()], evidence)) == 1

    def test_a_failed_fetch_credits_nothing(self) -> None:
        evidence = [
            ToolEvidence(
                tool_name="WebFetch", target=f"https://arxiv.org/abs/{self.PAPER}", ok=False
            )
        ]
        assert len(self._checker().check_tool_honesty([self._entry()], evidence)) == 1

    def test_a_raw_target_match_cannot_satisfy_a_scholarly_claim(self) -> None:
        """The exact-string path is the other half of the forgery.

        An entry naming a `canonical_id` is matched by identifier only, so pointing
        `source_url` at a local file the model really did read no longer backs a claim
        about a paper it never fetched.
        """
        local = "/repo/docs/design.md"
        evidence = [ToolEvidence(tool_name="Read", target=local, ok=True)]
        entry = self._entry(source_url=local)
        assert len(self._checker().check_tool_honesty([entry], evidence)) == 1

    def test_an_entry_with_no_scholarly_claim_still_accepts_an_exact_match(self) -> None:
        """Reading the artifact under review is legitimate evidence about the artifact."""
        local = "/repo/docs/design.md"
        evidence = [ToolEvidence(tool_name="Read", target=local, ok=True)]
        entry = self._entry(canonical_id="", source_url=local)
        assert self._checker().check_tool_honesty([entry], evidence) == []

    def test_a_doi_is_creditable_only_from_the_doi_resolver(self) -> None:
        doi = "10.1038/s41586-024-07711-7"
        entry = self._entry(canonical_id=doi, source_url=f"https://doi.org/{doi}")
        good = [ToolEvidence(tool_name="WebFetch", target=f"https://doi.org/{doi}", ok=True)]
        publisher = [
            ToolEvidence(
                tool_name="WebFetch",
                target="https://www.nature.com/articles/s41586-024-07711-7",
                ok=True,
            )
        ]
        assert self._checker().check_tool_honesty([entry], good) == []
        assert len(self._checker().check_tool_honesty([entry], publisher)) == 1

    def test_authorities_are_configurable_for_a_mirror(self) -> None:
        """An institutional mirror or proxy is a settings change, not a code change.

        The proxied form still has to canonicalize, so the identifier must be recoverable
        from the URL; what the setting changes is *which host* is allowed to vouch for it.
        """
        checker = VerificationLogChecker(
            author_names=set(),
            identifier_authorities={"arxiv": ("arxiv.org", "mirror.example")},
        )
        proxied = f"https://mirror.example/arxiv.org/abs/{self.PAPER}"
        assert VerificationLogChecker(author_names=set()).check_tool_honesty(
            [self._entry()], [ToolEvidence(tool_name="WebFetch", target=proxied, ok=True)]
        ), "the proxy must be refused until it is configured as an authority"
        assert (
            checker.check_tool_honesty(
                [self._entry()], [ToolEvidence(tool_name="WebFetch", target=proxied, ok=True)]
            )
            == []
        )

    def test_an_unfetched_entry_is_never_a_defect(self) -> None:
        """Tool honesty judges claims of retrieval; an entry making none is not a claim.

        `status=verified` is itself gated on `fetched=True` by the model validator, so an
        honest unfetched entry carries a weaker status.
        """
        entry = self._entry(fetched=False, status="unverified_flagged")
        assert self._checker().check_tool_honesty([entry], []) == []
