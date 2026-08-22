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
