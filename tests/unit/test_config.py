"""HarnessSettings precedence: init > env > YAML config file > defaults."""

from pathlib import Path

import pytest

from creative_agent.config import HarnessSettings
from creative_agent.errors import ConfigError


def test_defaults_load_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREATIVE_AGENT_CONFIG", raising=False)
    settings = HarnessSettings()
    assert settings.model == "inherit"
    assert settings.permission_mode == "dontAsk"
    assert Path("data/oracles") in settings.oracle_search_paths


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREATIVE_AGENT_MODEL", "opus")
    monkeypatch.setenv("CREATIVE_AGENT_MAX_TURNS", "3")
    settings = HarnessSettings()
    assert settings.model == "opus"
    assert settings.max_turns == 3


def test_config_file_under_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("model: sonnet\nmax_turns: 5\n", encoding="utf-8")
    monkeypatch.setenv("CREATIVE_AGENT_CONFIG", str(config))
    monkeypatch.setenv("CREATIVE_AGENT_MODEL", "opus")
    settings = HarnessSettings()
    assert settings.model == "opus"  # env beats file
    assert settings.max_turns == 5  # file beats default


def test_init_kwargs_beat_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREATIVE_AGENT_MODEL", "opus")
    settings = HarnessSettings(model="haiku")
    assert settings.model == "haiku"


def test_non_mapping_config_file_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("- just\n- a list\n", encoding="utf-8")
    monkeypatch.setenv("CREATIVE_AGENT_CONFIG", str(config))
    with pytest.raises(ConfigError):
        HarnessSettings()
