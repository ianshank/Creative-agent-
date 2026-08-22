"""Canonical identifier extraction — URL noise must collapse to one identity."""

import pytest

from creative_agent.harness.canonical import all_identifiers, canonicalize


@pytest.mark.parametrize(
    "variant",
    [
        "https://arxiv.org/abs/2208.11173",
        "https://arxiv.org/pdf/2208.11173",
        "http://arxiv.org/abs/2208.11173v2",
        "arXiv:2208.11173",
        "2208.11173",
        "see arxiv.org/abs/2208.11173 for details",
    ],
)
def test_arxiv_variants_collapse(variant: str) -> None:
    assert canonicalize(variant) == "arxiv:2208.11173"


@pytest.mark.parametrize(
    "variant",
    [
        "https://doi.org/10.1038/s41586-024-07711-7",
        "doi:10.1038/s41586-024-07711-7",
        "DOI: 10.1038/s41586-024-07711-7",
        "10.1038/s41586-024-07711-7",
    ],
)
def test_doi_variants_collapse(variant: str) -> None:
    assert canonicalize(variant) == "doi:10.1038/s41586-024-07711-7"


@pytest.mark.parametrize("text", [None, "", "https://example.com/paper.pdf", "hello world"])
def test_non_identifiers_return_none(text: str | None) -> None:
    assert canonicalize(text) is None


def test_all_identifiers_finds_every_mention() -> None:
    text = (
        "We build on arXiv:2208.11173 and https://doi.org/10.1038/s41586-024-07711-7, "
        "plus https://arxiv.org/pdf/2202.03466v1."
    )
    assert all_identifiers(text) == {
        "arxiv:2208.11173",
        "doi:10.1038/s41586-024-07711-7",
        "arxiv:2202.03466",
    }


class TestOldStyleArxiv:
    """Pre-2007 ids (archive/YYMMNNN); a corpus spanning eras must match these too."""

    @pytest.mark.parametrize(
        "variant",
        [
            "math/0211159",
            "arXiv:math/0211159",
            "https://arxiv.org/abs/math/0211159",
            "https://arxiv.org/pdf/math/0211159v2",
        ],
    )
    def test_old_style_ids_canonicalize(self, variant: str) -> None:
        assert canonicalize(variant) == "arxiv:math/0211159"

    def test_subject_class_ids(self) -> None:
        assert canonicalize("arXiv:cs.LG/0102030") == "arxiv:cs.lg/0102030"


class TestDoiEdgeCases:
    def test_parenthesised_doi_matches_from_both_forms(self) -> None:
        """Old Wiley ids contain parens; URL and bare forms must be one identity."""
        doi = "10.1002/(SICI)1097-0142(19960401)77:7"
        assert canonicalize(f"https://doi.org/{doi}") == canonicalize(doi)
        assert canonicalize(doi) == f"doi:{doi.lower()}"

    def test_trailing_sentence_punctuation_stripped(self) -> None:
        assert canonicalize("see doi:10.1038/s41586-024-07711-7.") == (
            "doi:10.1038/s41586-024-07711-7"
        )


class TestProperties:
    @pytest.mark.parametrize(
        "value",
        [
            "https://arxiv.org/abs/2208.11173v3",
            "math/0211159",
            "https://doi.org/10.1038/s41586-024-07711-7",
        ],
    )
    def test_canonicalize_is_idempotent(self, value: str) -> None:
        once = canonicalize(value)
        assert once is not None
        assert canonicalize(once) == once
