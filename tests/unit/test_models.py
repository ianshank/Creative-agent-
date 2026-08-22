"""Model-layer validation: severity parsing, keys, honesty validators, cross-refs."""

import pytest
from pydantic import ValidationError

from creative_agent.models.findings import FindingKey, Severity, normalize_slug
from creative_agent.models.oracle import (
    ArtifactClassRule,
    AttributionConfig,
    DecisionLogGrammar,
    DecisionTrap,
    FreshnessMeta,
    GateDefinition,
    GatePolicy,
    LabelElement,
    OakConformanceSpec,
    OracleRow,
    OracleTable,
    ProtocolConfig,
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


class TestOracleCrossReferenceInvariants:
    """Every cross-field invariant needs a negative test, or the validator is decoration."""

    def test_non_gap_row_without_sources_rejected(self) -> None:
        with pytest.raises(ValidationError, match="need at least one source"):
            make_row(sources=[])

    def test_duplicate_gate_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate gate names"):
            GatePolicy(
                gates=[
                    GateDefinition(name="observable", description="a"),
                    GateDefinition(name="observable", description="b"),
                ],
                missing_any_severity=Severity.MAJOR,
                hand_asserted_severity=Severity.MAJOR,
            )

    def test_duplicate_artifact_class_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate artifact class names"):
            make_oracle(
                artifact_classes=[
                    ArtifactClassRule(name="design"),
                    ArtifactClassRule(name="design"),
                ]
            )

    def test_decision_trap_must_reference_a_required_decision(self) -> None:
        with pytest.raises(ValidationError, match="not in required_decisions"):
            make_oracle(
                required_decisions=["DEC-S1"],
                decision_traps=[DecisionTrap(decision_id="DEC-S9", trap="x")],
            )

    def test_label_conformance_doctrine_ref_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="not a known row"):
            make_oracle(
                oak_conformance=OakConformanceSpec(
                    label_pattern="X",
                    doctrine_ref="Z9",
                    missing_severity=Severity.MAJOR,
                    features=[LabelElement(label="f", patterns=["f"])],
                    stages=[LabelElement(label="s", patterns=["s"])],
                )
            )

    def test_placeholder_row_id_must_not_look_like_a_row(self) -> None:
        with pytest.raises(ValidationError, match="collide with a real doctrine row"):
            make_oracle(
                protocol=ProtocolConfig(
                    escalation_cycle=3, unverified_marker="[U]", placeholder_row_id="G0"
                )
            )

    def test_offline_artifact_class_must_be_declared(self) -> None:
        with pytest.raises(ValidationError, match="not a declared artifact class"):
            make_oracle(
                protocol=ProtocolConfig(
                    escalation_cycle=3,
                    unverified_marker="[U]",
                    offline_artifact_class="nonexistent",
                )
            )

    def test_uncompilable_impersonation_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError, match="invalid regex"):
            AttributionConfig(impersonation_patterns=["(unclosed"])

    def test_decision_grammar_requires_named_groups(self) -> None:
        with pytest.raises(ValidationError, match="named groups"):
            DecisionLogGrammar(entry_pattern=r"^## DEC-\d+", confirmed_status="CONFIRMED")

    def test_row_lookup_raises_for_unknown_id(self) -> None:
        with pytest.raises(KeyError):
            make_oracle().row("ZZ9")

    def test_severity_parse_rejects_unsupported_type(self) -> None:
        with pytest.raises(ValueError, match="cannot parse severity"):
            Severity.parse(object())
