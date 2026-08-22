"""OracleLoader hardening + the shipped sutton oracle's named invariants."""

from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from creative_agent.errors import ConfigError, CreativeAgentError, OracleValidationError
from creative_agent.harness.oracle import OracleLoader
from creative_agent.models.oracle import OracleTable
from tests.factories import make_oracle

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
        authors = sutton.row("D2").sources[0].authors
        assert authors == ["Michael Bowling", "John D. Martin", "Will Dabney", "David Abel"]

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
