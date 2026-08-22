"""Durable review state (DEC-F4) and cycle escalation.

State files are markdown with YAML front matter: the front matter is machine truth
(schema_version'd, BC-tested against frozen fixtures), the body a human-readable summary.
Writes are atomic (tmp + rename) under an advisory lock; corrupt state is a typed error
with an explicit --reset-state escape hatch, never a silent cycle reset.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError

from creative_agent.errors import StateCorruptError
from creative_agent.harness.logging import get_logger, log_event
from creative_agent.models.findings import Severity
from creative_agent.models.oracle import OracleTable
from creative_agent.models.review import ReviewResult
from creative_agent.models.state import EscalationEvent, ReviewState

SUPPORTED_STATE_SCHEMA_VERSIONS = {1}
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
        lock_path = self._dir / f".{artifact_id}.lock"
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

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
        if version not in SUPPORTED_STATE_SCHEMA_VERSIONS:
            raise StateCorruptError(
                f"{path}: unsupported state schema_version {version!r}; "
                f"supported: {sorted(SUPPORTED_STATE_SCHEMA_VERSIONS)}"
            )
        try:
            return ReviewState.model_validate(raw)
        except ValidationError as exc:
            raise StateCorruptError(f"{path}: invalid state: {exc}") from exc

    def save(self, state: ReviewState, summary_markdown: str = "") -> Path:
        path = self.path_for(state.artifact_id)
        front = yaml.safe_dump(state.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        content = f"---\n{front}---\n\n{summary_markdown}".rstrip() + "\n"
        with self._locked(state.artifact_id):
            tmp_path = path.with_suffix(".md.tmp")
            try:
                tmp_path.write_text(content, encoding="utf-8", newline="\n")
                os.replace(tmp_path, path)
            except OSError:
                # Never leave a partial temp file behind to be mistaken for state.
                tmp_path.unlink(missing_ok=True)
                raise
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

    def reset(self, artifact_id: str) -> None:
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
