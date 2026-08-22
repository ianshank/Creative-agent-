"""Model-layer validation: severity parsing, keys, honesty validators, cross-refs."""

import pytest
from pydantic import ValidationError

from creative_agent.models.findings import FindingKey, Severity, normalize_slug
from creative_agent.models.oracle import (
    ArtifactClassRule,
    FreshnessMeta,
    OracleRow,
    OracleTable,
)
from creative_agent.models.sweeps import RowDisposition
from creative_agent.models.verification import VerificationEntry
from tests.factories import make_oracle, make_row, make_source, make_verification


class TestSeverity:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("blocker", Severity.BLOCKER),
            ("Major", Severity.MAJOR),
            ("MINOR", Severity.MINOR),
            (" info ", Severity.INFO),
            (2, Severity.MAJOR),
            (Severity.INFO, Severity.INFO),
        ],
    )
    def test_parse(self, raw: object, expected: Severity) -> None:
        assert Severity.parse(raw) is expected

    def test_parse_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown severity"):
            Severity.parse("catastrophic")

    def test_ordering(self) -> None:
        assert Severity.BLOCKER > Severity.MAJOR > Severity.MINOR > Severity.INFO


class TestFindingKey:
    def test_slug_is_normalized_on_construction(self) -> None:
        key = FindingKey(row_id="D2", slug="No Preference Relation!")
        assert key.slug == "no-preference-relation"
        assert key.render() == "D2+no-preference-relation"

    def test_unicode_folds_to_stable_ascii(self) -> None:
        # The attribution/recurrence machinery must not be defeated by diacritics.
        assert normalize_slug("Hernández-García") == normalize_slug("Hernandez-Garcia")

    def test_empty_slug_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FindingKey(row_id="D1", slug="!!!")


class TestVerificationHonesty:
    def test_verified_requires_fetched(self) -> None:
        with pytest.raises(ValidationError, match="fetched=True"):
            make_verification(fetched=False)

    def test_verified_requires_source(self) -> None:
        with pytest.raises(ValidationError, match="source_url or canonical_id"):
            make_verification(source_url=None, canonical_id=None)

    def test_guessing_status_needs_guessing_confidence(self) -> None:
        with pytest.raises(ValidationError, match="confidence=Guessing"):
            make_verification(status="guessing", fetched=False, confidence="Likely")

    def test_unverified_flagged_is_valid_without_fetch(self) -> None:
        entry = VerificationEntry(
            assertion="Reference could not be fetched",
            confidence="Likely",
            status="unverified_flagged",
        )
        assert not entry.fetched


class TestRowDisposition:
    def test_na_requires_reason(self) -> None:
        with pytest.raises(ValidationError, match="requires na_reason"):
            RowDisposition(row_id="D3", verdict="not_applicable")

    def test_na_with_reason_ok(self) -> None:
        d = RowDisposition(row_id="D3", verdict="not_applicable", na_reason="no data claims")
        assert d.na_reason


class TestOracleModels:
    def test_row_id_pattern_enforced(self) -> None:
        with pytest.raises(ValidationError):
            make_row("d1")

    def test_pr_source_requires_identifier(self) -> None:
        with pytest.raises(ValidationError, match="needs doi, arxiv_id, or url"):
            make_row(sources=[make_source(arxiv_id=None, doi=None, url=None, tier="PR")])

    def test_talk_source_needs_no_identifier(self) -> None:
        row = make_row(tier="T", sources=[make_source(arxiv_id=None, doi=None, url=None, tier="T")])
        assert row.tier == "T"

    def test_gap_row_needs_no_sources(self) -> None:
        row = OracleRow(
            id="D6a",
            principle="disclosed gap",
            sources=[],
            tier="NONE",
            check="do not demand impossible citations",
            failure_mode="silent regime transfer",
            disclosed_gap=True,
        )
        assert row.disclosed_gap

    def test_duplicate_row_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate row ids"):
            make_oracle(rows=[make_row("D1"), make_row("D1")])

    def test_unknown_gate_reference_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown gates"):
            make_oracle(
                artifact_classes=[
                    ArtifactClassRule(name="blueprint", requires_gates=["nonexistent"])
                ]
            )

    def test_extra_keys_forbidden(self) -> None:
        oracle = make_oracle()
        payload = oracle.model_dump(mode="json")
        payload["surprise"] = True
        with pytest.raises(ValidationError):
            OracleTable.model_validate(payload)


class TestStaleness:
    def test_verified_row_is_fresh(self) -> None:
        oracle = make_oracle()
        assert not oracle.rows[0].is_stale(oracle.freshness)

    def test_unverified_row_stale_only_at_budget(self) -> None:
        row = make_row(sources=[make_source(verified=False)])
        fresh = FreshnessMeta(
            last_rebaselined="2026-01-01",
            rebaseline_count=1,
            max_rebaselines_without_verification=2,
        )
        stale = fresh.model_copy(update={"rebaseline_count": 2})
        assert not row.is_stale(fresh)
        assert row.is_stale(stale)

    def test_gap_row_never_stale(self) -> None:
        oracle = make_oracle()
        gap = OracleRow(
            id="D6a",
            principle="gap",
            sources=[],
            tier="NONE",
            check="c",
            failure_mode="f",
            disclosed_gap=True,
        )
        assert not gap.is_stale(oracle.freshness.model_copy(update={"rebaseline_count": 99}))
