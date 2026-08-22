"""CLI behavior via CliRunner + the main() exit-code wrapper."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from creative_agent import __version__, cli
from creative_agent.cli import app, main
from creative_agent.errors import ConfigError, ExitCode

runner = CliRunner()


class TestBasicCommands:
    def test_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_oracles_list_finds_packaged_sutton(self) -> None:
        result = runner.invoke(app, ["oracles", "list"])
        assert result.exit_code == 0
        assert "sutton" in result.output

    def test_oracles_validate_all(self) -> None:
        result = runner.invoke(app, ["oracles", "validate", "--all"])
        assert result.exit_code == 0
        assert "ok: sutton" in result.output

    def test_oracles_validate_by_name(self) -> None:
        result = runner.invoke(app, ["oracles", "validate", "sutton"])
        assert result.exit_code == 0

    def test_validate_without_args_is_config_error(self) -> None:
        result = runner.invoke(app, ["oracles", "validate"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_unknown_oracle_is_config_error(self) -> None:
        result = runner.invoke(app, ["oracles", "validate", "nonexistent"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_invalid_local_oracle_fails_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "bad.yaml").write_text("schema_version: 1\noracle_id: bad\n", "utf-8")
        monkeypatch.setenv("CREATIVE_AGENT_ORACLE_SEARCH_PATHS", f'["{tmp_path}"]')
        result = runner.invoke(app, ["oracles", "validate", "--all"])
        assert result.exit_code == int(ExitCode.CONFIG_ERROR)


class TestMainWrapper:
    def test_main_maps_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["creative-agent", "version"])
        assert main() == int(ExitCode.CLEAN)

    def test_main_maps_config_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["creative-agent", "oracles", "validate", "nope"])
        assert main() == int(ExitCode.CONFIG_ERROR)

    def test_main_maps_typed_error_raised_outside_handlers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> None:
            raise ConfigError("bad settings")

        monkeypatch.setattr(cli, "_settings", boom)
        monkeypatch.setattr("sys.argv", ["creative-agent", "oracles", "validate", "--all"])
        assert main() == int(ExitCode.CONFIG_ERROR)

    def test_main_maps_unexpected_error_to_code_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> None:
            raise RuntimeError("surprise")

        monkeypatch.setattr(cli, "_settings", boom)
        monkeypatch.setattr("sys.argv", ["creative-agent", "oracles", "list"])
        assert main() == int(ExitCode.UNEXPECTED_ERROR)
