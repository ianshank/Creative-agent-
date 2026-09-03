"""Schema migration seams for the durable read formats (DEC-F18).

Every durable format carries a `schema_version` and both loaders rejected an unknown one
outright, so there was no point at which an older file could be upgraded on read. This
module is that point: an ordered chain of raw-mapping upgrade steps applied between the
version check and `model_validate`.

The chain is empty today — v1 is current for both formats, so `migrate` is an identity
pass whose plumbing is tested. The first real migration registers one step rather than
introducing a mechanism under time pressure.

Steps operate on raw dicts, never on models. `models/base.SchemaModel` sets
`extra="forbid"`, so a step written against the live model would silently rot the moment
the model gained a field: it must emit exactly the field set of the version it upgrades
*to*, which is why each future step needs a frozen fixture of the version it upgrades
*from*.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from creative_agent.harness.logging import get_logger, log_event

_LOG = get_logger(__name__)

# One upgrade step: takes the raw mapping at version N, returns it at version N+1.
MigrationStep = Callable[[dict[str, Any]], dict[str, Any]]


class MigrationChainError(Exception):
    """The chain itself is malformed — a programming error, not bad input data."""


class MigrationChain:
    """Upgrades a raw mapping from any supported version to the current one.

    Callers keep ownership of their own error types: `supported_versions` lets a loader
    reject an unknown version with its own message and exception class (an oracle file and
    a state file fail differently), and `migrate` then assumes the version is readable.
    """

    def __init__(
        self,
        *,
        format_name: str,
        current_version: int,
        steps: Mapping[int, MigrationStep] | None = None,
    ) -> None:
        if current_version < 1:
            raise MigrationChainError(f"{format_name}: current_version must be >= 1")
        self._format_name = format_name
        self._current_version = current_version
        self._steps: dict[int, MigrationStep] = dict(steps or {})
        for version in self._steps:
            if not 1 <= version < current_version:
                raise MigrationChainError(
                    f"{format_name}: migration step from version {version} is outside "
                    f"1..{current_version - 1}"
                )

    @property
    def current_version(self) -> int:
        return self._current_version

    @property
    def supported_versions(self) -> set[int]:
        """Versions readable today: the current one, plus every older version with an
        unbroken chain of steps up to it.

        A gap truncates the range rather than silently skipping a version, so a
        half-registered chain cannot claim to read a file it would mangle.
        """
        readable = {self._current_version}
        version = self._current_version - 1
        while version >= 1 and version in self._steps:
            readable.add(version)
            version -= 1
        return readable

    def migrate(
        self, raw: dict[str, Any], *, from_version: int, source: str = ""
    ) -> dict[str, Any]:
        """Apply every step from `from_version` up to the current version.

        Returns the mapping unchanged when it is already current, which is the whole of
        today's behaviour. The input is not mutated.
        """
        if from_version not in self.supported_versions:
            raise MigrationChainError(
                f"{self._format_name}: no migration path from version {from_version}; "
                f"readable: {sorted(self.supported_versions)}"
            )
        if from_version == self._current_version:
            return raw
        migrated = dict(raw)
        for version in range(from_version, self._current_version):
            migrated = self._steps[version](migrated)
            migrated["schema_version"] = version + 1
        log_event(
            _LOG,
            logging.INFO,
            "schema.migrated",
            format=self._format_name,
            from_version=from_version,
            to_version=self._current_version,
            source=source,
        )
        return migrated
