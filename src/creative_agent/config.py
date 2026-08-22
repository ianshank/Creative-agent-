"""Harness settings — every tunable lives here, never as a literal at a call site.

Sources, highest precedence first: init kwargs > environment (CREATIVE_AGENT_*) >
YAML file named by CREATIVE_AGENT_CONFIG > field defaults. Field defaults are framework
constants with env override, per the no-hard-coded-values policy: anything a deployment
might tune is a field, not a literal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from creative_agent.errors import ConfigError
from creative_agent.harness.security import DEFAULT_BLOCKED_HOST_SUFFIXES

PermissionMode = Literal["default", "acceptEdits", "plan", "dontAsk", "auto", "bypassPermissions"]

_CONFIG_ENV_VAR = "CREATIVE_AGENT_CONFIG"


class _YamlConfigSource(PydanticBaseSettingsSource):
    """Reads the YAML file named by CREATIVE_AGENT_CONFIG, if any."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._values: dict[str, Any] = {}
        config_path = os.environ.get(_CONFIG_ENV_VAR)
        if config_path:
            try:
                text = Path(config_path).read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(
                    f"cannot read {_CONFIG_ENV_VAR} file {config_path}: {exc}"
                ) from exc
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
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
        # Env values reach the validators as raw strings so list fields can accept the
        # documented `a,b` / `a:b` forms as well as JSON (see _split_scalar_lists).
        enable_decoding=False,
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
    # Which of those tools produce evidence that can back a `fetched=True` verification
    # entry. WebSearch is deliberately excluded: snippets support existence, not content.
    fetch_tool_names: list[str] = Field(default_factory=lambda: ["WebFetch", "Read"])
    permission_mode: PermissionMode = "dontAsk"

    # Data search paths. Packaged resources are the fallback; earlier entries win.
    oracle_search_paths: list[Path] = Field(default_factory=lambda: [Path("data/oracles")])
    prompt_search_paths: list[Path] = Field(default_factory=lambda: [Path("data/prompts")])

    # State and logs.
    review_log_dir: Path = Path("docs/review-log")
    decision_log_filename: str = "docs/decision-log.md"

    # Plugin defaults, so the harness can be rebranded without a code edit.
    default_agent: str = "sutton-review"

    # Oracle loader hardening bounds.
    max_oracle_bytes: int = 2_000_000
    max_oracle_depth: int = 32
    max_artifact_bytes: int = 20_000_000

    # Citation resolution (oracles rebaseline).
    arxiv_api_url: str = "https://export.arxiv.org/api/query"
    citation_timeout_seconds: float = 30.0

    # Observability (DEC-F10). Level and format are configuration, never literals at a
    # call site; --verbose/--debug on the CLI raise the level for one invocation.
    log_level: str = "WARNING"
    log_format: str = "text"

    # Rendering limits (output laundering, DEC-F9).
    max_prose_chars: int = 4_000

    # Fetch-allowlist policy (DEC-F9). Hosts harvested from the untrusted artifact are
    # filtered so a planted URL cannot point the session at an internal target; a
    # deployment that genuinely reviews against an internal mirror can extend the
    # suffix list or (deliberately) opt out.
    blocked_host_suffixes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLOCKED_HOST_SUFFIXES)
    )
    allow_internal_fetch_hosts: bool = False

    @field_validator(
        "agent_tools",
        "fetch_tool_names",
        "oracle_search_paths",
        "prompt_search_paths",
        "blocked_host_suffixes",
        mode="before",
    )
    @classmethod
    def _split_scalar_lists(cls, value: Any) -> Any:
        """Accept `a,b` and `a:b` for list fields, not just JSON.

        pydantic-settings only parses JSON for complex types, which made the documented
        env syntax (`CREATIVE_AGENT_AGENT_TOOLS=Read,Grep`) fail with a parse error.
        """
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON list: {exc}") from exc
            separator = os.pathsep if os.pathsep in text else ","
            return [part.strip() for part in text.split(separator) if part.strip()]
        return value

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
