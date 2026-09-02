"""Schema migration seams (DEC-F18).

The chain is empty today, so these tests are about the plumbing being correct *before* the
first real migration needs it: an identity pass that does not mutate its input, a version
range derived from registered steps rather than asserted by hand, and a refusal to claim
readability across a gap.
"""

from __future__ import annotations

from typing import Any

import pytest

from creative_agent.harness.migrations import (
    MigrationChain,
    MigrationChainError,
)
from creative_agent.harness.oracle import ORACLE_MIGRATIONS, SUPPORTED_ORACLE_SCHEMA_VERSIONS
from creative_agent.harness.state import STATE_MIGRATIONS, SUPPORTED_STATE_SCHEMA_VERSIONS


def _bump(field: str) -> Any:
    def step(raw: dict[str, Any]) -> dict[str, Any]:
        return {**raw, field: True}

    return step


class TestSupportedVersions:
    def test_a_chain_with_no_steps_reads_only_the_current_version(self) -> None:
        chain = MigrationChain(format_name="demo", current_version=3)
        assert chain.supported_versions == {3}

    def test_registered_steps_extend_the_readable_range_downwards(self) -> None:
        chain = MigrationChain(
            format_name="demo",
            current_version=3,
            steps={1: _bump("from_v1"), 2: _bump("from_v2")},
        )
        assert chain.supported_versions == {1, 2, 3}

    def test_a_gap_truncates_the_range_instead_of_skipping_a_version(self) -> None:
        """Claiming to read v1 with no v1→v2 step would silently mangle a v1 file.

        The chain must under-promise: with only a v2→v3 step registered, v1 is not
        readable even though a v1 file would superficially validate.
        """
        chain = MigrationChain(format_name="demo", current_version=3, steps={2: _bump("from_v2")})
        assert chain.supported_versions == {2, 3}

    def test_a_step_outside_the_version_range_is_a_programming_error(self) -> None:
        with pytest.raises(MigrationChainError):
            MigrationChain(format_name="demo", current_version=2, steps={5: _bump("nope")})

    def test_current_version_must_be_at_least_one(self) -> None:
        with pytest.raises(MigrationChainError):
            MigrationChain(format_name="demo", current_version=0)


class TestMigrate:
    def test_a_current_version_mapping_passes_through_unchanged(self) -> None:
        chain = MigrationChain(format_name="demo", current_version=1)
        raw = {"schema_version": 1, "value": "x"}
        assert chain.migrate(raw, from_version=1) is raw

    def test_steps_run_in_order_and_stamp_the_new_version(self) -> None:
        order: list[int] = []

        def step(version: int) -> Any:
            def run(raw: dict[str, Any]) -> dict[str, Any]:
                order.append(version)
                return {**raw, f"seen_v{version}": True}

            return run

        chain = MigrationChain(
            format_name="demo", current_version=3, steps={1: step(1), 2: step(2)}
        )
        result = chain.migrate({"schema_version": 1}, from_version=1)

        assert order == [1, 2]
        assert result["schema_version"] == 3
        assert result["seen_v1"] and result["seen_v2"]

    def test_the_input_mapping_is_not_mutated(self) -> None:
        """A loader may still want the raw mapping for an error message."""
        chain = MigrationChain(format_name="demo", current_version=2, steps={1: _bump("touched")})
        raw = {"schema_version": 1}
        chain.migrate(raw, from_version=1)
        assert raw == {"schema_version": 1}

    def test_an_unreachable_version_raises_rather_than_validating_garbage(self) -> None:
        chain = MigrationChain(format_name="demo", current_version=2)
        with pytest.raises(MigrationChainError):
            chain.migrate({"schema_version": 1}, from_version=1)


class TestBothDurableFormatsAreWired:
    """The seam exists for the two formats that are actually read back.

    The report contract is deliberately excluded: it is written and never parsed, so a
    migration chain for it would be ceremony (DEC-F18).
    """

    @pytest.mark.parametrize(
        ("chain", "exported"),
        [
            (ORACLE_MIGRATIONS, SUPPORTED_ORACLE_SCHEMA_VERSIONS),
            (STATE_MIGRATIONS, SUPPORTED_STATE_SCHEMA_VERSIONS),
        ],
    )
    def test_exported_supported_versions_come_from_the_chain(
        self, chain: MigrationChain, exported: set[int]
    ) -> None:
        """The module constant and the chain cannot drift, because one derives from the other."""
        assert exported == chain.supported_versions

    @pytest.mark.parametrize("chain", [ORACLE_MIGRATIONS, STATE_MIGRATIONS])
    def test_version_one_is_current_today(self, chain: MigrationChain) -> None:
        assert chain.current_version == 1
        assert chain.supported_versions == {1}
