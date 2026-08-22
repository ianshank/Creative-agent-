"""Oracle loading: safe, bounded YAML → validated OracleTable (DEC-F2)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from creative_agent.errors import ConfigError, OracleValidationError
from creative_agent.models.oracle import OracleTable

SUPPORTED_ORACLE_SCHEMA_VERSIONS = {1}
DEFAULT_MAX_YAML_DEPTH = 32


def _check_depth(node: object, depth: int = 0, max_depth: int = DEFAULT_MAX_YAML_DEPTH) -> None:
    if depth > max_depth:
        raise OracleValidationError(f"oracle YAML exceeds max nesting depth {max_depth}")
    if isinstance(node, dict):
        for value in node.values():
            _check_depth(value, depth + 1, max_depth)
    elif isinstance(node, list):
        for value in node:
            _check_depth(value, depth + 1, max_depth)


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
        if version not in SUPPORTED_ORACLE_SCHEMA_VERSIONS:
            raise OracleValidationError(
                f"{path}: unsupported schema_version {version!r}; "
                f"supported: {sorted(SUPPORTED_ORACLE_SCHEMA_VERSIONS)}"
            )
        try:
            return OracleTable.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            raise OracleValidationError(
                f"{path}: invalid oracle at {location or '<root>'}: {first['msg']} "
                f"({exc.error_count()} error(s) total)"
            ) from exc

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
