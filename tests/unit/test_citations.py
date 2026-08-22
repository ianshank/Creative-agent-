"""ArxivCitationResolver (respx-mocked Atom API) and OracleRebaseliner."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from creative_agent.harness.citations import (
    ArxivCitationResolver,
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
