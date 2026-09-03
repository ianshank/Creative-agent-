"""ArxivCitationResolver (respx-mocked Atom API) and OracleRebaseliner."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import respx

from creative_agent.harness.citations import (
    ArxivCitationResolver,
    CompositeCitationResolver,
    CrossrefCitationResolver,
    NullCitationResolver,
    OracleRebaseliner,
)
from creative_agent.harness.clock import FixedClock
from creative_agent.harness.protocols import ResolutionResult
from tests.factories import make_oracle, make_row, make_source

API = "https://arxiv.example/api/query"
NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def atom(title: str, authors: list[str]) -> str:
    author_xml = "".join(f"<author><name>{name}</name></author>" for name in authors)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<entry><title>{title}</title>{author_xml}</entry></feed>"
    )


def resolver() -> ArxivCitationResolver:
    return ArxivCitationResolver(API, timeout_seconds=5.0)


class TestResolver:
    @respx.mock
    async def test_matching_authors_resolve(self) -> None:
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Jane Doe"])))
        result = await resolver().resolve(make_source(authors=["Jane Doe"]))
        assert result.status == "resolved"
        assert result.resolved_title == "A Paper"

    @respx.mock
    async def test_diacritics_do_not_cause_mismatch(self) -> None:
        respx.get(API).mock(
            return_value=httpx.Response(200, text=atom("A Paper", ["J. Fernando Hernández-García"]))
        )
        result = await resolver().resolve(make_source(authors=["J. Fernando Hernandez-Garcia"]))
        assert result.status == "resolved"

    @respx.mock
    async def test_wrong_author_list_is_mismatch(self) -> None:
        """The v1 defect class: a fabricated author list must surface loudly."""
        respx.get(API).mock(
            return_value=httpx.Response(
                200, text=atom("Settling the Reward Hypothesis", ["Michael Bowling", "Will Dabney"])
            )
        )
        result = await resolver().resolve(
            make_source(authors=["Michael Bowling", "Richard S. Sutton"])
        )
        assert result.status == "mismatch"
        assert "sutton" in result.detail

    @respx.mock
    async def test_http_error_is_unreachable(self) -> None:
        respx.get(API).mock(return_value=httpx.Response(500))
        assert (await resolver().resolve(make_source())).status == "unreachable"

    @respx.mock
    async def test_empty_feed_is_unreachable(self) -> None:
        respx.get(API).mock(
            return_value=httpx.Response(
                200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
        )
        assert (await resolver().resolve(make_source())).status == "unreachable"

    async def test_source_without_arxiv_id_skipped(self) -> None:
        source = make_source(arxiv_id=None, url="https://example.org/x")
        assert (await resolver().resolve(source)).status == "skipped"

    @respx.mock
    async def test_arxiv_error_entry_is_unreachable_not_mismatch(self) -> None:
        """A typo'd id must not be reported as the fabricated-citation defect class."""
        error_feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<id>http://arxiv.org/api/errors#incorrect_id_format</id>"
            "<title>Error</title><author><name>arXiv api core</name></author>"
            "</entry></feed>"
        )
        respx.get(API).mock(return_value=httpx.Response(200, text=error_feed))
        result = await resolver().resolve(make_source(authors=["Jane Doe"]))
        assert result.status == "unreachable"

    @respx.mock
    async def test_source_without_declared_authors_is_not_verified(self) -> None:
        """An undeclared author list must not be laundered into a verified source."""
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Someone"])))
        result = await resolver().resolve(make_source(authors=[]))
        assert result.status == "unreachable"
        assert "declares no authors" in result.detail

    @respx.mock
    async def test_blank_author_string_does_not_crash(self) -> None:
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Jane Doe"])))
        result = await resolver().resolve(make_source(authors=["   ", "Jane Doe"]))
        assert result.status == "resolved"

    async def test_null_resolver_skips(self) -> None:
        assert (await NullCitationResolver().resolve(make_source())).status == "skipped"


class TestRebaseliner:
    @respx.mock
    async def test_resolution_updates_verification_and_freshness(self) -> None:
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Jane Doe"])))
        oracle = make_oracle(
            rows=[make_row("D1", sources=[make_source(verified=False, last_verified=None)])]
        )
        rebaseliner = OracleRebaseliner(resolver(), FixedClock(NOW))
        updated, report = await rebaseliner.rebaseline(oracle)
        source = updated.rows[0].sources[0]
        assert source.verified and source.last_verified == NOW.date()
        assert updated.freshness.rebaseline_count == oracle.freshness.rebaseline_count + 1
        assert updated.freshness.last_rebaselined == NOW.date()
        assert any("resolved" in line for line in report)

    @respx.mock
    async def test_mismatch_marks_unverified_and_reports(self) -> None:
        respx.get(API).mock(
            return_value=httpx.Response(200, text=atom("A Paper", ["Somebody Else"]))
        )
        oracle = make_oracle(rows=[make_row("D1", sources=[make_source(verified=True)])])
        assert oracle.rows[0].sources[0].last_verified is not None
        updated, report = await OracleRebaseliner(resolver(), FixedClock(NOW)).rebaseline(oracle)
        source = updated.rows[0].sources[0]
        assert not source.verified
        # A stale verification date on an unverified source would misreport the row as
        # recently checked, so the date is cleared with the flag.
        assert source.last_verified is None
        assert any("MISMATCH" in line for line in report)

    async def test_unreachable_leaves_source_untouched(self) -> None:
        oracle = make_oracle(rows=[make_row("D1", sources=[make_source(verified=False)])])

        class FailingResolver:
            async def resolve(self, ref: object) -> ResolutionResult:
                return ResolutionResult("unreachable", detail="timeout")

        updated, report = await OracleRebaseliner(FailingResolver(), FixedClock(NOW)).rebaseline(
            oracle
        )
        assert not updated.rows[0].sources[0].verified
        assert any("unreachable" in line for line in report)
        # The freshness counter still advances: staleness pressure is the design.
        assert updated.freshness.rebaseline_count == 1


CROSSREF = "https://crossref.example/works"
UA = "creative-agent-test/0"


def crossref_resolver() -> CrossrefCitationResolver:
    return CrossrefCitationResolver(CROSSREF, timeout_seconds=5.0, user_agent=UA)


def crossref_body(authors: list[dict[str, str]], title: str = "Loss of plasticity") -> dict:
    return {"message": {"title": [title], "author": authors}}


class TestCrossrefResolver:
    """Four of the shipped oracle's sources carry a DOI and no arXiv id.

    `ArxivCitationResolver` returns `skipped` whenever `arxiv_id` is absent, so nothing
    could ever verify them. Once the staleness cap fires (DEC-F13) that is a severity bug:
    a peer-reviewed paper's findings get capped at Minor because nobody transcribed a
    resolvable identifier, not because the evidence is weak.
    """

    DOI = "10.1038/s41586-024-07711-7"

    async def test_a_source_without_a_doi_is_skipped(self) -> None:
        result = await crossref_resolver().resolve(make_source(doi=None, arxiv_id="2001.1"))
        assert result.status == "skipped"

    @respx.mock
    async def test_a_matching_author_list_resolves(self) -> None:
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(
                200, json=crossref_body([{"given": "Jane", "family": "Doe"}])
            )
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["Jane Doe"])
        )
        assert result.status == "resolved"
        assert result.resolved_authors == ["Jane Doe"]

    @respx.mock
    async def test_a_differing_author_list_is_a_mismatch(self) -> None:
        """The defect class this machinery exists to catch."""
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(
                200,
                json=crossref_body(
                    [{"given": "Jane", "family": "Doe"}, {"given": "Ada", "family": "Byron"}]
                ),
            )
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["Jane Doe"])
        )
        assert result.status == "mismatch"

    @respx.mock
    async def test_surnames_are_diacritic_folded(self) -> None:
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(
                200, json=crossref_body([{"given": "J. Fernando", "family": "Hernández-García"}])
            )
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["J. Fernando Hernandez-Garcia"])
        )
        assert result.status == "resolved"

    @respx.mock
    async def test_a_transport_failure_is_unreachable_never_a_mismatch(self) -> None:
        """Reporting a network failure as MISMATCH would accuse a correct citation."""
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(404)
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["Jane Doe"])
        )
        assert result.status == "unreachable"

    @respx.mock
    async def test_an_unexpected_payload_is_unreachable(self) -> None:
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(200, json={"not": "what we expect"})
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["Jane Doe"])
        )
        assert result.status == "unreachable"

    @respx.mock
    async def test_a_source_declaring_no_authors_is_not_auto_verified(self) -> None:
        """Otherwise an undeclared author list launders itself past the staleness cap."""
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(
                200, json=crossref_body([{"given": "Jane", "family": "Doe"}])
            )
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=[])
        )
        assert result.status == "unreachable"

    @respx.mock
    async def test_a_record_with_no_authors_to_diff_is_unreachable(self) -> None:
        respx.get(f"{CROSSREF}/{self.DOI.replace('/', '%2F')}").mock(
            return_value=httpx.Response(200, json=crossref_body([]))
        )
        result = await crossref_resolver().resolve(
            make_source(doi=self.DOI, arxiv_id=None, authors=["Jane Doe"])
        )
        assert result.status == "unreachable"

    @respx.mock
    async def test_the_doi_is_url_encoded(self) -> None:
        """A DOI contains slashes and may contain parentheses; both must survive."""
        doi = "10.1016/S0004-3702(99)00052-1"
        route = respx.get(f"{CROSSREF}/{quote(doi, safe='')}").mock(
            return_value=httpx.Response(
                200, json=crossref_body([{"given": "Richard", "family": "Sutton"}])
            )
        )
        await crossref_resolver().resolve(
            make_source(doi=doi, arxiv_id=None, authors=["Richard S. Sutton"])
        )
        assert route.called


class TestCompositeResolver:
    """A source resolves against whichever backend can identify it."""

    async def test_the_first_non_skipping_backend_wins(self) -> None:
        composite = CompositeCitationResolver(
            [NullCitationResolver(), _Always("resolved", "second")]
        )
        result = await composite.resolve(make_source())
        assert result.status == "resolved"
        assert result.detail == "second"

    async def test_all_skipped_reports_skipped_with_every_reason(self) -> None:
        """A source with no resolvable identifier must not borrow another backend's failure."""
        composite = CompositeCitationResolver(
            [_Always("skipped", "no arXiv id"), _Always("skipped", "no DOI")]
        )
        result = await composite.resolve(make_source())
        assert result.status == "skipped"
        assert "no arXiv id" in result.detail
        assert "no DOI" in result.detail

    async def test_an_unreachable_backend_stops_the_chain(self) -> None:
        """Unreachable is an answer, not an abstention; falling through would hide it."""
        composite = CompositeCitationResolver(
            [_Always("unreachable", "arxiv down"), _Always("resolved", "never reached")]
        )
        result = await composite.resolve(make_source())
        assert result.status == "unreachable"

    async def test_an_empty_composite_reports_skipped(self) -> None:
        result = await CompositeCitationResolver([]).resolve(make_source())
        assert result.status == "skipped"


class _Always:
    """A resolver that always answers the same way."""

    def __init__(self, status: str, detail: str) -> None:
        self._status = status
        self._detail = detail

    async def resolve(self, ref: object) -> ResolutionResult:
        return ResolutionResult(self._status, detail=self._detail)
