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
from typing import Any, Literal, get_origin

import yaml
from pydantic import Field, ValidationInfo, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from creative_agent.errors import ConfigError
from creative_agent.harness.canonical import DEFAULT_IDENTIFIER_AUTHORITIES
from creative_agent.harness.security import DEFAULT_BLOCKED_HOST_SUFFIXES

# `bypassPermissions` is deliberately absent. `harness/llm/claude_sdk.py`'s module
# docstring has always said the adapter uses "headless permissions (permission_mode from
# settings + restrictive allowed_tools — never bypassPermissions)", and the only thing
# enforcing that was the default value: the mode was in this Literal, so a settings file
# could select it and it reached `ClaudeAgentOptions` unmodified, turning the whole
# DEC-F15 hook into advice. The test that claimed to check this asserted
# `"bypass" not in options.permission_mode` on a fixture that set `dontAsk` — it
# re-asserted its own input (DEC-F28). Removing the value from the type makes the
# docstring true and the settings error a validation error at load time.
PermissionMode = Literal["default", "acceptEdits", "plan", "dontAsk", "auto"]

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

    # Tools the PreToolUse hook allows without a target check, because they carry no path
    # or host to scope (DEC-F15). Everything not handled explicitly and not named here is
    # denied: the hook used to allow any tool it did not recognise. WebSearch is here with
    # its residual risk accepted and documented — its query is unconstrained, so an
    # artifact that can steer the model's search terms has an outbound channel; DEC-F9
    # already refuses to credit WebSearch results as `fetched`. A multi-tenant deployment
    # should empty this list.
    unscoped_tools: list[str] = Field(default_factory=lambda: ["WebSearch"])

    # Tools that are part of the SDK's own request/response protocol rather than a
    # capability granted to the review (DEC-F20). `StructuredOutput` is how the SDK
    # delivers a structured answer, so denying it does not harden the review — it stops
    # the review from receiving any answer at all, which is exactly what deny-by-default
    # did until the first live end-to-end run. Kept separate from `unscoped_tools` because
    # the guidance for that list is "a multi-tenant deployment should empty it": emptying
    # this one breaks the harness instead, so conflating the two would make the security
    # advice self-defeating. Permitting the envelope grants nothing — the payload it
    # carries is schema-validated on arrival and laundered before it reaches a report.
    protocol_tools: list[str] = Field(default_factory=lambda: ["StructuredOutput"])

    # Which hosts may vouch for a scholarly identifier in the tool-honesty check
    # (DEC-F12). A fetch only credits an identifier when it came from that identifier's
    # own registrar, so a decoy URL mentioning `arxiv.org/abs/<id>` proves nothing. Data
    # rather than literals: an institutional mirror or a DOI proxy belongs here, not in a
    # code change. Accepts JSON from the environment.
    identifier_authority_hosts: dict[str, list[str]] = Field(
        default_factory=lambda: {
            scheme: list(hosts) for scheme, hosts in DEFAULT_IDENTIFIER_AUTHORITIES.items()
        }
    )

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

    # Citation resolution (oracles rebaseline). The Crossref backend resolves the DOI-only
    # sources the arXiv resolver skips — four of the shipped oracle's sources carry a DOI
    # and no arXiv id, so no code path could verify them at all before (DEC-F13's cap makes
    # that a severity bug, not just a gap).
    arxiv_api_url: str = "https://export.arxiv.org/api/query"
    crossref_api_url: str = "https://api.crossref.org/works"
    citation_timeout_seconds: float = 30.0
    # Crossref asks API clients to identify themselves; anonymous traffic is rate-limited
    # into a slower pool. Deliberately not defaulted to the operator's email — an address
    # belongs in an outbound header only when someone puts it there on purpose.
    citation_user_agent: str = "creative-agent/0.1 (+https://github.com/ianshank/Creative-agent-)"

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

    @field_validator("identifier_authority_hosts", mode="before")
    @classmethod
    def _parse_mapping(cls, value: Any) -> Any:
        """Accept a JSON object from the environment for the one mapping-valued setting.

        `enable_decoding=False` hands every env value to the validators as a raw string
        (see `_split_scalar_lists`), so without this the documented
        `CREATIVE_AGENT_IDENTIFIER_AUTHORITY_HOSTS='{"arxiv": ["mirror.example"]}'` form
        would fail to parse.
        """
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON mapping: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("expected a JSON object mapping identifier scheme to hosts")
            return parsed
        return value

    @field_validator("*", mode="before")
    @classmethod
    def _split_scalar_lists(cls, value: Any, info: ValidationInfo) -> Any:
        """Accept `a,b` and `a:b` for every list field, not just JSON.

        pydantic-settings only parses JSON for complex types, which made the documented env
        syntax (`CREATIVE_AGENT_AGENT_TOOLS=Read,Grep`) fail with a parse error.

        Applied to every list-annotated field rather than to a named six. The named form was
        the same defect this codebase keeps finding elsewhere — a list of things to include,
        which is wrong the moment someone adds the seventh. `protocol_tools` was that
        seventh: DEC-F20 justified it as a list "so that an SDK that renames or adds a
        protocol tool is a settings change", and the documented env shorthand rejected it
        with a type error, so that was true of the YAML file and false of the environment.
        Selecting by *shape* means the next list field works the day it is added.
        """
        field = cls.model_fields.get(info.field_name or "")
        if field is None or get_origin(field.annotation) is not list:
            return value
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
