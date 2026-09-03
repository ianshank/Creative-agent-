"""VerificationLogChecker: completeness, tool honesty, attribution sweep."""

import pytest

from creative_agent.harness import canonical, citations, policy, security, sourcequality
from creative_agent.harness import verification as verification_module
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


class TestEvidenceBindingCannotBeOptedOutOf:
    """DEC-F12's first cut branched on `canonical_id` alone, which made it opt-out.

    `canonical_id` is `str | None` and the model writes the entry, so the party the control
    exists to constrain chose which branch judged it: omit the field, and a local `Read` of
    an author-controlled filename backed a claim about a paper again. A source that carries
    a scholarly identifier is now judged by identifier however the entry is shaped, and an
    evidence target that carries one is never plain-string evidence.
    """

    PAPER = "2401.12345"

    def _checker(self) -> VerificationLogChecker:
        return VerificationLogChecker(author_names=set())

    def test_omitting_canonical_id_does_not_reopen_the_local_read_forgery(self) -> None:
        local = f"refs/arxiv.org/abs/{self.PAPER}.md"
        entry = VerificationEntry(
            assertion="Sutton et al. say X",
            row_id="D1",
            source_url=local,
            canonical_id=None,
            confidence="Certain",
            fetched=True,
            status="verified",
        )
        evidence = [ToolEvidence(tool_name="Read", target=local, ok=True)]
        assert len(self._checker().check_tool_honesty([entry], evidence)) == 1

    def test_a_non_canonicalizing_canonical_id_does_not_fall_through(self) -> None:
        """`arXiv 2401.12345` with a space does not canonicalize; it must not be a bypass."""
        local = f"refs/arxiv.org/abs/{self.PAPER}.md"
        entry = VerificationEntry(
            assertion="Sutton et al. say X",
            row_id="D1",
            source_url=local,
            canonical_id=f"arXiv {self.PAPER}",
            confidence="Certain",
            fetched=True,
            status="verified",
        )
        evidence = [ToolEvidence(tool_name="Read", target=local, ok=True)]
        assert len(self._checker().check_tool_honesty([entry], evidence)) == 1

    def test_a_genuinely_non_scholarly_entry_still_passes(self) -> None:
        """Reading the artifact under review remains legitimate evidence about it."""
        local = "/repo/docs/design.md"
        entry = VerificationEntry(
            assertion="the document says so",
            row_id="D1",
            source_url=local,
            canonical_id=None,
            confidence="Certain",
            fetched=True,
            status="verified",
        )
        evidence = [ToolEvidence(tool_name="Read", target=local, ok=True)]
        assert self._checker().check_tool_honesty([entry], evidence) == []


class TestIdentifierMustComeFromTheServedResource:
    """Authority binding constrained *which host*, not *what was retrieved*.

    arxiv.org and doi.org are on every computed allowlist unconditionally and both return
    200 for arbitrary junk, so the decoy simply moved to the registrar. Only the host and
    path may carry the identifier now — a query string or fragment is the model's choice of
    string, not evidence of what was served.
    """

    PAPER = "2401.12345"

    def _entry(self) -> VerificationEntry:
        return VerificationEntry(
            assertion="a",
            row_id="D1",
            canonical_id=f"arXiv:{self.PAPER}",
            source_url=f"https://arxiv.org/abs/{self.PAPER}",
            confidence="Certain",
            fetched=True,
            status="verified",
        )

    @pytest.mark.parametrize(
        "target",
        [
            "https://arxiv.org/?x=arxiv.org/abs/2401.12345",
            "https://arxiv.org/list/cs.LG/recent#arxiv.org/abs/2401.12345",
            "https://doi.org/?q=arxiv.org/abs/2401.12345",
        ],
    )
    def test_an_identifier_in_a_query_or_fragment_is_not_retrieval(self, target: str) -> None:
        evidence = [ToolEvidence(tool_name="WebFetch", target=target, ok=True)]
        assert len(VerificationLogChecker(set()).check_tool_honesty([self._entry()], evidence)) == 1

    def test_arxivs_own_doi_prefix_through_doi_org_is_credited(self) -> None:
        """`10.48550` is arXiv's DOI prefix and the standard modern citation form.

        Demanding that the host be an authority for the *scheme canonicalization picked*
        refused a fetch of the paper through doi.org — the resolver DEC-F12 explicitly
        blesses — and failed the review with exit 3. A reviewer that always refuses is not
        a reviewer.
        """
        evidence = [
            ToolEvidence(
                tool_name="WebFetch",
                target=f"https://doi.org/10.48550/arXiv.{self.PAPER}",
                ok=True,
            )
        ]
        assert VerificationLogChecker(set()).check_tool_honesty([self._entry()], evidence) == []

    def test_a_port_does_not_defeat_the_match(self) -> None:
        evidence = [
            ToolEvidence(
                tool_name="WebFetch", target=f"https://arxiv.org:443/abs/{self.PAPER}", ok=True
            )
        ]
        assert VerificationLogChecker(set()).check_tool_honesty([self._entry()], evidence) == []


class TestTheTwoUntestedHonestyBranches:
    """G3 and G4: the two branches nothing held, in opposite directions.

    Both were provably unheld — deleting either left the whole suite green. The cause is
    the same in both cases: `tests/factories.py::make_verification` supplies a default
    `canonical_id`, so every test that reaches this checker through the factory exits at
    the *canonical_id* branch and the two below never execute. A control tested only
    through a factory is tested on the factory's defaults, not on its own inputs.

    They fail in opposite directions, which is why both matter. Without the negative case
    a fabricated non-scholarly claim publishes; without the positive case an honest
    reviewer citing a paper by URL alone is failed with exit 3, and a reviewer that always
    refuses is not a reviewer.
    """

    PAPER = "2401.12345"

    def _entry(self, **overrides: object) -> VerificationEntry:
        fields: dict[str, object] = {
            "assertion": "the notes say so",
            "row_id": "D1",
            "source_url": "docs/notes.md",
            "canonical_id": None,
            "confidence": "Certain",
            "fetched": True,
            "status": "verified",
        }
        fields.update(overrides)
        return VerificationEntry(**fields)  # type: ignore[arg-type]

    def test_a_non_scholarly_claim_with_no_matching_fetch_is_a_defect(self) -> None:
        """G3, the negative side: `fetched=True` with nothing fetched at all.

        No `canonical_id`, a plain-file `source_url`, and an empty evidence list. The
        model asserts it read something it never touched and mints `status=verified`.
        """
        defects = VerificationLogChecker(author_names=set()).check_tool_honesty([self._entry()], [])
        assert len(defects) == 1
        assert "no successful fetch" in defects[0]

    def test_evidence_for_a_different_target_does_not_satisfy_the_claim(self) -> None:
        """The near miss: something *was* read, just not the thing claimed."""
        evidence = [ToolEvidence(tool_name="Read", target="docs/other.md", ok=True)]
        defects = VerificationLogChecker(author_names=set()).check_tool_honesty(
            [self._entry()], evidence
        )
        assert len(defects) == 1

    def test_a_fetched_source_url_identifier_is_credited_without_a_canonical_id(self) -> None:
        """G4, the positive side: an honest entry that names its paper only by URL.

        This is the common shape for a review that cites a preprint and genuinely fetched
        it. Judging it by identifier is right; judging it a fabrication is a false refusal
        of a correct document.
        """
        entry = self._entry(source_url=f"https://arxiv.org/abs/{self.PAPER}")
        evidence = [
            ToolEvidence(
                tool_name="WebFetch", target=f"https://arxiv.org/abs/{self.PAPER}", ok=True
            )
        ]
        assert (
            VerificationLogChecker(author_names=set()).check_tool_honesty([entry], evidence) == []
        )


class TestOnePolicyHasOneDefinition:
    """DEC-F25: constants two modules must agree on are one object, not two literals.

    Identity, not equality. Two equal frozensets satisfy `==` on the day they are written
    and drift the day one of them is edited, which is the failure this guards: loosening
    the fetch schemes without loosening the evidence schemes (or the reverse) reopens
    `file://arxiv.org/...` on exactly one of the two paths, and which one is worse depends
    on which was edited.
    """

    def test_the_fetch_and_evidence_scheme_sets_are_the_same_object(self) -> None:
        assert security.ALLOWED_FETCH_SCHEMES is policy.EVIDENCE_SCHEMES
        assert canonical._EVIDENCE_SCHEMES is policy.EVIDENCE_SCHEMES

    def test_the_url_pattern_is_the_same_object_in_both_readers(self) -> None:
        """The bibliography checker and the allowlist harvester must agree on what a URL
        is, or a citation is judged by one and unreachable by the other."""
        assert security._URL is policy.URL_PATTERN
        assert sourcequality._URL is policy.URL_PATTERN

    def test_name_folding_is_the_same_function_for_the_sweep_and_the_diff(self) -> None:
        """A name that folds one way for the impersonation sweep and another for the
        rebaseline diff is a rebaseline that clears an author the sweep still flags."""
        assert verification_module._fold is policy.fold_name
        assert citations._fold_surname is policy.fold_surname
        assert policy.fold_surname("Alberto Hernández-García") == "hernandez-garcia"
