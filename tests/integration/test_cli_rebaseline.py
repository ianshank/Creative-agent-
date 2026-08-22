"""The oracles rebaseline CLI against a mocked arXiv API and a local oracle override."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
import yaml
from typer.testing import CliRunner

from creative_agent.cli import app
from tests.factories import make_oracle, make_row, make_source
from tests.unit.test_citations import atom

runner = CliRunner()
API = "https://arxiv.mock/api/query"


@pytest.fixture()
def oracle_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    oracle = make_oracle(
        rows=[
            make_row(
                "D1",
                sources=[make_source(authors=["Jane Doe"], verified=False, last_verified=None)],
            )
        ]
    )
    path = tmp_path / "mini.yaml"
    path.write_text(
        yaml.safe_dump(oracle.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("CREATIVE_AGENT_ORACLE_SEARCH_PATHS", f'["{tmp_path}"]')
    monkeypatch.setenv("CREATIVE_AGENT_ARXIV_API_URL", API)
    return tmp_path


class TestRebaselineCli:
    @respx.mock
    def test_dry_run_reports_without_writing(self, oracle_dir: Path) -> None:
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Jane Doe"])))
        before = (oracle_dir / "mini.yaml").read_text(encoding="utf-8")
        result = runner.invoke(app, ["oracles", "rebaseline", "mini", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "resolved" in result.output
        assert "dry run" in result.output
        assert (oracle_dir / "mini.yaml").read_text(encoding="utf-8") == before

    @respx.mock
    def test_write_updates_file_and_freshness(self, oracle_dir: Path) -> None:
        respx.get(API).mock(return_value=httpx.Response(200, text=atom("A Paper", ["Jane Doe"])))
        result = runner.invoke(app, ["oracles", "rebaseline", "mini"])
        assert result.exit_code == 0, result.output
        updated = yaml.safe_load((oracle_dir / "mini.yaml").read_text(encoding="utf-8"))
        assert updated["freshness"]["rebaseline_count"] == 1
        assert updated["rows"][0]["sources"][0]["verified"] is True
        # The rewritten file must still validate.
        check = runner.invoke(app, ["oracles", "validate", "mini"])
        assert check.exit_code == 0, check.output

    @respx.mock
    def test_author_mismatch_exits_nonzero(self, oracle_dir: Path) -> None:
        respx.get(API).mock(
            return_value=httpx.Response(200, text=atom("A Paper", ["Somebody Else"]))
        )
        result = runner.invoke(app, ["oracles", "rebaseline", "mini"])
        assert result.exit_code == 1
        assert "MISMATCH" in result.output
