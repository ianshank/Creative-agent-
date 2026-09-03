"""Oracle loading: safe, bounded YAML → validated OracleTable (DEC-F2)."""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from creative_agent.errors import ConfigError, OracleValidationError
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.harness.migrations import MigrationChain
from creative_agent.models.oracle import OracleTable

CURRENT_ORACLE_SCHEMA_VERSION = 1
# Migration seam (DEC-F18). No steps yet: v1 is current, so this is an identity pass whose
# plumbing is tested. The first `schema_version: 2` registers a step here.
ORACLE_MIGRATIONS = MigrationChain(
    format_name="oracle", current_version=CURRENT_ORACLE_SCHEMA_VERSION, steps={}
)
# Kept as a module constant: it is part of this module's public surface and is imported by
# tests and by the CLI's diagnostics. Derived from the chain so the two can never drift.
SUPPORTED_ORACLE_SCHEMA_VERSIONS = ORACLE_MIGRATIONS.supported_versions
DEFAULT_MAX_YAML_DEPTH = 32

_LOG = get_logger(__name__)


def _check_depth(node: object, depth: int = 0, max_depth: int = DEFAULT_MAX_YAML_DEPTH) -> None:
    if depth > max_depth:
        raise OracleValidationError(f"oracle YAML exceeds max nesting depth {max_depth}")
    if isinstance(node, dict):
        for value in node.values():
            _check_depth(value, depth + 1, max_depth)
    elif isinstance(node, list):
        for value in node:
            _check_depth(value, depth + 1, max_depth)


def _warn_on_uncapped_unverified_rows(table: OracleTable, path: Path) -> None:
    """Warn when unresolved doctrine can still carry a Blocker (DEC-F13).

    A row with no verified source relies on the staleness cap to hold its findings down.
    A nonzero `freshness.max_rebaselines_without_verification` suppresses that cap, which
    is how ten unresolved rows in the shipped oracle went uncapped without anyone noticing.
    Silence about weak doctrine is the failure mode; this makes it greppable.
    """
    uncapped = table.unverified_blocker_rows()
    if not uncapped or table.freshness.max_rebaselines_without_verification <= 0:
        return
    log_event(
        _LOG,
        logging.WARNING,
        "oracle.unverified_rows_uncapped",
        oracle_id=table.oracle_id,
        path=str(path),
        rows=",".join(uncapped),
        row_count=len(uncapped),
        grace_budget=table.freshness.max_rebaselines_without_verification,
        rebaseline_count=table.freshness.rebaseline_count,
    )


class OracleLoader:
    """Finds and validates oracle tables across a search path, packaged data last."""

    def __init__(
        self,
        search_paths: list[Path],
        max_bytes: int,
        max_depth: int = DEFAULT_MAX_YAML_DEPTH,
    ) -> None:
        self._search_paths = search_paths
        self._max_bytes = max_bytes
        self._max_depth = max_depth

    def _packaged_dir(self) -> Path:
        return Path(str(resources.files("creative_agent").joinpath("data", "oracles")))

    def candidate_files(self) -> list[Path]:
        """All oracle YAML files, earlier search paths shadowing later ones by oracle_id."""
        files: list[Path] = []
        for directory in [*self._search_paths, self._packaged_dir()]:
            if directory.is_dir():
                files.extend(sorted(directory.glob("*.yaml")))
        return files

    def load_file(self, path: Path) -> OracleTable:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigError(f"cannot read oracle file {path}: {exc}") from exc
        if len(data) > self._max_bytes:
            raise OracleValidationError(f"{path}: oracle file exceeds {self._max_bytes} bytes")
        try:
            raw = yaml.safe_load(data.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            raise OracleValidationError(f"{path}: not valid YAML: {exc}") from exc
        if raw is None:
            raise OracleValidationError(f"{path}: empty oracle file")
        if not isinstance(raw, dict):
            raise OracleValidationError(f"{path}: oracle root must be a mapping")
        _check_depth(raw, max_depth=self._max_depth)
        version = raw.get("schema_version")
        if version not in ORACLE_MIGRATIONS.supported_versions:
            raise OracleValidationError(
                f"{path}: unsupported schema_version {version!r}; "
                f"supported: {sorted(ORACLE_MIGRATIONS.supported_versions)}"
            )
        raw = ORACLE_MIGRATIONS.migrate(raw, from_version=version, source=str(path))
        try:
            table = OracleTable.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            raise OracleValidationError(
                f"{path}: invalid oracle at {location or '<root>'}: {first['msg']} "
                f"({exc.error_count()} error(s) total)"
            ) from exc
        _warn_on_uncapped_unverified_rows(table, path)
        return table

    def load(self, oracle_id: str) -> OracleTable:
        """Load by oracle_id; first match across the search path wins."""
        return self.find(oracle_id)[1]

    def find(self, oracle_id: str) -> tuple[Path, OracleTable]:
        """Like load, but also returns the file the oracle came from."""
        seen: list[str] = []
        for path in self.candidate_files():
            table = self.load_file(path)
            if table.oracle_id == oracle_id:
                return path, table
            seen.append(table.oracle_id)
        raise ConfigError(
            f"oracle {oracle_id!r} not found; available: {sorted(set(seen)) or 'none'}"
        )

    def load_all(self) -> dict[str, OracleTable]:
        """Load every discoverable oracle, first-found winning per oracle_id."""
        tables: dict[str, OracleTable] = {}
        for path in self.candidate_files():
            table = self.load_file(path)
            tables.setdefault(table.oracle_id, table)
        return tables
