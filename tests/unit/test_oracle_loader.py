"""OracleLoader hardening + the shipped sutton oracle's named invariants."""

import logging
from datetime import date
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from creative_agent.errors import ConfigError, CreativeAgentError, OracleValidationError
from creative_agent.harness.oracle import OracleLoader
from creative_agent.models.oracle import FreshnessMeta, OracleTable
from tests.factories import make_oracle, make_row, make_source

MAX_BYTES = 2_000_000


def write_oracle(directory: Path, table: OracleTable, name: str = "mini.yaml") -> Path:
    path = directory / name
    path.write_text(
        yaml.safe_dump(table.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    return path


@pytest.fixture()
def loader(tmp_path: Path) -> OracleLoader:
    return OracleLoader([tmp_path], MAX_BYTES)


class TestLoaderHappyPath:
    def test_roundtrip(self, tmp_path: Path, loader: OracleLoader) -> None:
        write_oracle(tmp_path, make_oracle())
        table = loader.load("mini")
        assert table.oracle_id == "mini"
        assert table.row("D1").tier == "PR"

    def test_search_path_shadows_packaged(self, tmp_path: Path, loader: OracleLoader) -> None:
        override = make_oracle(oracle_id="sutton", name="local override")
        write_oracle(tmp_path, override, "sutton-override.yaml")
        table = loader.load("sutton")
        assert table.name == "local override"

    def test_unknown_oracle_names_available(self, tmp_path: Path, loader: OracleLoader) -> None:
        write_oracle(tmp_path, make_oracle())
        with pytest.raises(ConfigError, match="mini"):
            loader.load("nope")


class TestLoaderHardening:
    def test_empty_file(self, tmp_path: Path, loader: OracleLoader) -> None:
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
        with pytest.raises(OracleValidationError, match="empty"):
            loader.load_file(tmp_path / "empty.yaml")

    def test_non_mapping_root(self, tmp_path: Path, loader: OracleLoader) -> None:
        (tmp_path / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(OracleValidationError, match="mapping"):
            loader.load_file(tmp_path / "list.yaml")

    def test_invalid_yaml(self, tmp_path: Path, loader: OracleLoader) -> None:
        (tmp_path / "bad.yaml").write_text("a: [unclosed", encoding="utf-8")
        with pytest.raises(OracleValidationError, match="not valid YAML"):
            loader.load_file(tmp_path / "bad.yaml")

    def test_oversized_file(self, tmp_path: Path) -> None:
        small_loader = OracleLoader([tmp_path], max_bytes=10)
        (tmp_path / "big.yaml").write_text("a: " + "x" * 100, encoding="utf-8")
        with pytest.raises(OracleValidationError, match="exceeds"):
            small_loader.load_file(tmp_path / "big.yaml")

    def test_excessive_nesting(self, tmp_path: Path, loader: OracleLoader) -> None:
        nested = "a:" + "".join(f"\n{'  ' * (i + 1)}a:" for i in range(40)) + " 1"
        (tmp_path / "deep.yaml").write_text(nested, encoding="utf-8")
        with pytest.raises(OracleValidationError, match="nesting depth"):
            loader.load_file(tmp_path / "deep.yaml")

    def test_alias_bomb_is_bounded(self, tmp_path: Path, loader: OracleLoader) -> None:
        # safe_load expands aliases but the depth/schema checks reject the result quickly.
        bomb = "a: &a [1, 1]\nb: &b [*a, *a]\nc: &c [*b, *b]\nschema_version: 1\n"
        (tmp_path / "bomb.yaml").write_text(bomb, encoding="utf-8")
        with pytest.raises(OracleValidationError):
            loader.load_file(tmp_path / "bomb.yaml")

    def test_unsupported_schema_version(self, tmp_path: Path, loader: OracleLoader) -> None:
        payload = make_oracle().model_dump(mode="json")
        payload["schema_version"] = 99
        (tmp_path / "v99.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.raises(OracleValidationError, match="schema_version"):
            loader.load_file(tmp_path / "v99.yaml")

    def test_error_names_file_and_key(self, tmp_path: Path, loader: OracleLoader) -> None:
        payload = make_oracle().model_dump(mode="json")
        payload["rows"][0]["tier"] = "XX"
        path = tmp_path / "badrow.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.raises(OracleValidationError, match=r"badrow\.yaml.*rows"):
            loader.load_file(path)

    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(text=st.text(max_size=400))
    def test_arbitrary_text_never_escapes_typed_errors(self, tmp_path: Path, text: str) -> None:
        loader = OracleLoader([tmp_path], MAX_BYTES)
        path = tmp_path / "fuzz.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            table = loader.load_file(path)
        except CreativeAgentError:
            return
        assert isinstance(table, OracleTable)


class TestShippedSuttonOracle:
    """Named invariants on the product data file. Updating any of these requires a
    decision-log entry (see CLAUDE.md) — the friction is the feature: this is the
    fabricated-author-list tripwire the spec's own v1 failed on."""

    @pytest.fixture()
    def sutton(self) -> OracleTable:
        return OracleLoader([], MAX_BYTES).load("sutton")

    def test_thirteen_rows(self, sutton: OracleTable) -> None:
        assert len(sutton.rows) == 13
        assert [r.id for r in sutton.rows] == [
            "D1",
            "D2",
            "D3",
            "D4",
            "D5",
            "D6",
            "D6a",
            "D7",
            "D8",
            "D9",
            "D10",
            "D11",
            "D12",
        ]

    def test_d2_author_list_is_the_corrected_one(self, sutton: OracleTable) -> None:
        """Order matches arXiv:2212.10420's front matter, independently re-verified — the
        spec's own transcription had Dabney and Abel transposed (Bowling/Martin/Dabney/
        Abel instead of the paper's Bowling/Martin/Abel/Dabney), a live instance of the
        exact defect class this oracle exists to catch, not merely v1's wholly wrong
        Sutton-instead-of-Dabney insertion this test used to guard against alone."""
        authors = sutton.row("D2").sources[0].authors
        assert authors == ["Michael Bowling", "John D. Martin", "David Abel", "Will Dabney"]

    def test_d6a_is_a_disclosed_gap(self, sutton: OracleTable) -> None:
        row = sutton.row("D6a")
        assert row.disclosed_gap and row.tier == "NONE" and not row.sources

    def test_d7_verified_against_arxiv(self, sutton: OracleTable) -> None:
        source = sutton.row("D7").sources[0]
        assert source.arxiv_id == "2604.19033" and source.verified

    def test_blueprint_class_requires_safety_section(self, sutton: OracleTable) -> None:
        rule = next(c for c in sutton.artifact_classes if c.name == "deployment_blueprint")
        assert "safety" in rule.requires_sections
        assert "compute_budget" in rule.requires_gates

    def test_d1_d12_precedence_is_data(self, sutton: OracleTable) -> None:
        assert sutton.row("D1").precedence is not None
        assert sutton.row("D1").precedence.defer_to == "D12"

    def test_decision_traps_cover_dec_s6(self, sutton: OracleTable) -> None:
        assert any(t.decision_id == "DEC-S6" for t in sutton.decision_traps)

    def test_escalation_cycle_and_marker(self, sutton: OracleTable) -> None:
        assert sutton.protocol.escalation_cycle == 3
        assert sutton.protocol.unverified_marker.startswith("[Unverified")


class TestUnverifiedDoctrineIsVisible:
    """Silence about weak doctrine is how ten unresolved rows went uncapped (DEC-F13).

    A row with no verified source relies on the staleness cap to hold its findings down.
    A nonzero grace budget suppresses that cap, so the loader says so at WARNING with the
    row ids — a stable, greppable event rather than a comment nobody reads.
    """

    def _oracle_yaml(self, grace: int, verified: bool) -> str:
        table = make_oracle(
            rows=[make_row("D1", tier="PR", sources=[make_source(verified=verified)])],
            freshness=FreshnessMeta(
                last_rebaselined=date(2026, 1, 1),
                rebaseline_count=0,
                max_rebaselines_without_verification=grace,
            ),
        )
        return yaml.safe_dump(table.model_dump(mode="json"), sort_keys=False)

    def _load(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "mini.yaml"
        path.write_text(text, encoding="utf-8")
        OracleLoader([tmp_path], max_bytes=1_000_000).load_file(path)

    def test_a_grace_budget_over_unverified_blocker_rows_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="creative_agent"):
            self._load(tmp_path, self._oracle_yaml(grace=2, verified=False))
        assert any("oracle.unverified_rows_uncapped" in r.getMessage() for r in caplog.records)

    def test_no_warning_when_the_cap_will_actually_fire(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Budget 0 means the unverified row is capped, so there is nothing to warn about."""
        with caplog.at_level(logging.WARNING, logger="creative_agent"):
            self._load(tmp_path, self._oracle_yaml(grace=0, verified=False))
        assert not any("oracle.unverified_rows_uncapped" in r.getMessage() for r in caplog.records)

    def test_no_warning_when_every_blocker_row_is_verified(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="creative_agent"):
            self._load(tmp_path, self._oracle_yaml(grace=2, verified=True))
        assert not any("oracle.unverified_rows_uncapped" in r.getMessage() for r in caplog.records)

    def test_unverified_blocker_rows_ignores_non_blocker_tiers_and_disclosed_gaps(self) -> None:
        table = make_oracle(
            rows=[
                make_row("D1", tier="PR", sources=[make_source(verified=False)]),
                make_row("D2", tier="T", sources=[make_source(verified=False)]),
                make_row("D3", tier="NONE", disclosed_gap=True, sources=[]),
                make_row("D4", tier="AP", sources=[make_source(verified=True)]),
            ]
        )
        assert table.unverified_blocker_rows() == ["D1"]


class TestShippedOracleStalenessInvariants:
    """Named invariants on the product data, per CLAUDE.md.

    Engine behaviour is tested against synthetic oracles; these assert only facts about
    the shipped corpus that a careless edit would quietly reverse.
    """

    def _sutton(self) -> OracleTable:
        return OracleLoader([], max_bytes=5_000_000).load("sutton")

    def test_the_shipped_grace_budget_is_zero(self) -> None:
        assert self._sutton().freshness.max_rebaselines_without_verification == 0

    def test_every_unverified_row_is_actually_stale(self) -> None:
        """The whole point of DEC-F13: no unresolved row escapes the cap."""
        table = self._sutton()
        for row in table.rows:
            if row.disclosed_gap or row.has_verified_source():
                continue
            assert row.is_stale(table.freshness), f"{row.id} is unverified but not stale"

    def test_the_rebaselined_rows_carry_a_verification_date(self) -> None:
        """D5, D8 and D11 were resolved by hand against the papers' own title pages."""
        table = self._sutton()
        for row_id in ("D5", "D8", "D11"):
            row = table.row(row_id)
            assert row.has_verified_source(), f"{row_id} lost its verified source"
            assert any(s.verified and s.last_verified for s in row.sources), (
                f"{row_id} claims verification with no last_verified date"
            )
