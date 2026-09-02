"""Durable review state (DEC-F4) and cycle escalation.

State files are markdown with YAML front matter: the front matter is machine truth
(schema_version'd, BC-tested against frozen fixtures), the body a human-readable summary.
Writes are atomic (tmp + rename) under an advisory lock; corrupt state is a typed error
with an explicit --reset-state escape hatch, never a silent cycle reset.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError

from creative_agent.errors import StateConflictError, StateCorruptError
from creative_agent.harness.filelock import exclusive_lock, write_text_atomic
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.harness.migrations import MigrationChain
from creative_agent.models.findings import Severity
from creative_agent.models.oracle import OracleTable
from creative_agent.models.review import ReviewResult
from creative_agent.models.state import EscalationEvent, ReviewState

CURRENT_STATE_SCHEMA_VERSION = 1
# Migration seam (DEC-F18), sharing the chain helper with the oracle loader. No steps yet.
STATE_MIGRATIONS = MigrationChain(
    format_name="review-state", current_version=CURRENT_STATE_SCHEMA_VERSION, steps={}
)
SUPPORTED_STATE_SCHEMA_VERSIONS = STATE_MIGRATIONS.supported_versions
_FRONT_MATTER = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n", re.DOTALL)
_LOG = get_logger(__name__)


class FileStateStore:
    """Reads and writes docs/review-log/<artifact-id>.md."""

    def __init__(self, review_log_dir: Path) -> None:
        self._dir = review_log_dir

    def path_for(self, artifact_id: str) -> Path:
        return self._dir / f"{artifact_id}.md"

    @contextmanager
    def _locked(self, artifact_id: str) -> Iterator[None]:
        self._dir.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self._dir / f".{artifact_id}.lock"):
            yield

    def load(self, artifact_id: str) -> ReviewState:
        path = self.path_for(artifact_id)
        if not path.exists():
            return ReviewState(artifact_id=artifact_id)
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        if not match:
            raise StateCorruptError(
                f"{path}: no YAML front matter; use --reset-state to discard history"
            )
        try:
            raw = yaml.safe_load(match.group("yaml"))
        except yaml.YAMLError as exc:
            raise StateCorruptError(
                f"{path}: unparseable front matter ({exc}); use --reset-state"
            ) from exc
        if not isinstance(raw, dict):
            raise StateCorruptError(f"{path}: front matter is not a mapping")
        version = raw.get("schema_version")
        if version not in STATE_MIGRATIONS.supported_versions:
            raise StateCorruptError(
                f"{path}: unsupported state schema_version {version!r}; "
                f"supported: {sorted(STATE_MIGRATIONS.supported_versions)}"
            )
        raw = STATE_MIGRATIONS.migrate(raw, from_version=version, source=str(path))
        try:
            return ReviewState.model_validate(raw)
        except ValidationError as exc:
            raise StateCorruptError(f"{path}: invalid state: {exc}") from exc

    def save(
        self,
        state: ReviewState,
        summary_markdown: str = "",
        *,
        expected_cycle: int | None = None,
    ) -> Path:
        """Persist state atomically, optionally refusing a lost update (DEC-F14).

        `expected_cycle` is the cycle the caller *loaded*. When given, the stored cycle is
        re-read under the same lock that guards the write and a mismatch raises
        `StateConflictError`: a concurrent review has advanced the history this run's
        verdict was computed against, so writing would silently discard it and, worse,
        would publish an escalation decision made from a snapshot that no longer exists.

        The keyword defaults to `None` so every existing caller and test double keeps
        working — the protocol widens rather than breaking.
        """
        path = self.path_for(state.artifact_id)
        front = yaml.safe_dump(state.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        content = f"---\n{front}---\n\n{summary_markdown}".rstrip() + "\n"
        with self._locked(state.artifact_id):
            if expected_cycle is not None:
                self._assert_no_conflict(state.artifact_id, expected_cycle)
            write_text_atomic(path, content, tmp_suffix=".tmp")
        log_event(
            _LOG,
            logging.DEBUG,
            "state.saved",
            artifact_id=state.artifact_id,
            cycle=state.cycle,
            path=str(path),
            bytes=len(content),
        )
        return path

    def _assert_no_conflict(self, artifact_id: str, expected_cycle: int) -> None:
        """Raise if the on-disk cycle moved since the caller loaded it.

        Runs inside the write lock, so the check and the write it guards are indivisible.
        Corrupt state is reported as a conflict rather than crashing the save: either way
        this run must not publish over it, and `StateCorruptError` here would misattribute
        a concurrent writer's partial file to the caller's own state.
        """
        try:
            stored_cycle = self.load(artifact_id).cycle
        except StateCorruptError as exc:
            raise StateConflictError(
                f"{self.path_for(artifact_id)}: state became unreadable during this review "
                f"({exc}); nothing was written — re-run the review"
            ) from exc
        if stored_cycle == expected_cycle:
            return
        log_event(
            _LOG,
            logging.WARNING,
            "state.write_conflict",
            artifact_id=artifact_id,
            expected_cycle=expected_cycle,
            stored_cycle=stored_cycle,
        )
        raise StateConflictError(
            f"review state for {artifact_id!r} advanced from cycle {expected_cycle} to "
            f"{stored_cycle} while this review was running, so another review of the same "
            "artifact wrote first. This run's findings and its escalation decision were "
            "computed against the older history and would be wrong to publish; nothing was "
            "written. Re-run the review."
        )

    def reset(self, artifact_id: str) -> None:
        """Discard history for one artifact, under the same lock a write takes."""
        with self._locked(artifact_id):
            path = self.path_for(artifact_id)
            if path.exists():
                path.unlink()


class CycleEscalator:
    """Spec protocol step 1: recurring open Majors trigger a charter-review STOP."""

    def __init__(self, oracle: OracleTable) -> None:
        self._escalation_cycle = oracle.protocol.escalation_cycle

    def check(self, state: ReviewState, result: ReviewResult) -> EscalationEvent | None:
        current_cycle = state.cycle + 1
        if current_cycle < self._escalation_cycle:
            return None
        prior = state.open_major_keys()
        for finding in result.findings:
            if Severity.parse(finding.severity) < Severity.MAJOR:
                continue
            rendered = finding.key.render()
            prior_cycles = prior.get(rendered, [])
            if len(prior_cycles) + 1 >= self._escalation_cycle:
                all_cycles = [*prior_cycles, current_cycle]
                return EscalationEvent(
                    key=finding.key,
                    cycles=all_cycles,
                    message=(
                        f"Major finding {rendered} has recurred across cycles "
                        f"{all_cycles} with disposition still open. "
                        "STOP: charter review triggered — the decision passes to the "
                        "owner."
                    ),
                )
        return None
