"""Harness settings — every tunable lives here, never as a literal at a call site.

Sources, highest precedence first: init kwargs > environment (CREATIVE_AGENT_*) >
YAML file named by CREATIVE_AGENT_CONFIG > field defaults. Field defaults are framework
constants with env override, per the no-hard-coded-values policy: anything a deployment
might tune is a field, not a literal.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from creative_agent.errors import ConfigError

_CONFIG_ENV_VAR = "CREATIVE_AGENT_CONFIG"


class _YamlConfigSource(PydanticBaseSettingsSource):
    """Reads the YAML file named by CREATIVE_AGENT_CONFIG, if any."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._values: dict[str, Any] = {}
        config_path = os.environ.get(_CONFIG_ENV_VAR)
        if config_path:
            raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                raise ConfigError(f"config file {config_path} must contain a mapping")
            self._values = raw

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._values)


class HarnessSettings(BaseSettings):
    """Runtime configuration for the review harness."""

    model_config = SettingsConfigDict(
        env_prefix="CREATIVE_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # LLM backend. No model ID is hard-coded in harness code; "inherit" defers to the
    # Claude Agent SDK / Claude Code default for the authenticated account.
    model: str = "inherit"
    fallback_model: str | None = None
    max_turns: int = 12
    max_budget_usd: float | None = None
    max_regeneration_attempts: int = 2
    llm_timeout_seconds: float = 600.0

    # Tool scoping for the SDK session (threat model, DEC-F9). Names, not behavior:
    # the allowlist of *domains* is derived per-review from oracle + artifact data.
    agent_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
    )
    permission_mode: str = "dontAsk"

    # Data search paths. Packaged resources are the fallback; earlier entries win.
    oracle_search_paths: list[Path] = Field(default_factory=lambda: [Path("data/oracles")])
    prompt_search_paths: list[Path] = Field(default_factory=lambda: [Path("data/prompts")])

    # State and logs.
    review_log_dir: Path = Path("docs/review-log")
    decision_log_filename: str = "docs/decision-log.md"

    # Oracle loader hardening bounds.
    max_oracle_bytes: int = 2_000_000
    max_artifact_bytes: int = 20_000_000

    # Rendering limits (output laundering, DEC-F9).
    max_prose_chars: int = 4_000

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _YamlConfigSource(settings_cls),
            file_secret_settings,
        )
