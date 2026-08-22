"""Artifact identity and loading (DEC-F7)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from creative_agent.errors import ConfigError
from creative_agent.models.findings import normalize_slug

_REVIEW_ID = re.compile(r"^review-id:\s*(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)\s*$", re.MULTILINE)
# Tolerate a UTF-8 BOM and CRLF: a Windows checkout of the same document must resolve to
# the same artifact id, or its cycle history and escalation counter silently reset.
_FRONT_MATTER = re.compile(r"\A﻿?---\r?\n.*?\r?\n---\r?\n", re.DOTALL)

# An artifact id becomes a path segment (docs/review-log/<id>.md and the audit bundle
# directory), so it must be a single safe filename component: no separators, no traversal,
# no control characters, no leading dot. This is a security invariant, not a preference,
# so it lives in code rather than settings.
_SAFE_ARTIFACT_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
MAX_ARTIFACT_ID_LENGTH = 128


def validate_artifact_id(candidate: str, *, source: str) -> str:
    """Return the id if it is a safe single path segment, else raise ConfigError."""
    if not _SAFE_ARTIFACT_ID.match(candidate) or ".." in candidate:
        raise ConfigError(
            f"invalid artifact id {candidate!r} from {source}: ids become a filename, so "
            "they must start alphanumeric, contain only [A-Za-z0-9._-], avoid '..', and be "
            f"at most {MAX_ARTIFACT_ID_LENGTH} characters"
        )
    return candidate


def read_artifact(path: Path, max_bytes: int) -> str:
    """Read an artifact, refusing oversized files before loading them into memory."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfigError(f"cannot read artifact {path}: {exc}") from exc
    if size > max_bytes:
        raise ConfigError(f"artifact {path} exceeds {max_bytes} bytes ({size} bytes)")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read artifact {path}: {exc}") from exc
    if len(data) > max_bytes:  # e.g. a file that grew between stat and read
        raise ConfigError(f"artifact {path} exceeds {max_bytes} bytes")
    return data.decode("utf-8", errors="replace")


def content_sha256(text: str) -> str:
    """Digest over LF-normalized, BOM-stripped bytes so a CRLF or BOM-carrying checkout
    of the same document hashes identically."""
    normalized = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_artifact_id(path: Path, text: str, override: str | None) -> str:
    """--artifact-id flag > front-matter review-id > normalized filename slug.

    Every branch is validated: the override is operator input and the front-matter id
    comes from the untrusted artifact, so neither may escape the review-log directory.
    """
    if override:
        return validate_artifact_id(override, source="--artifact-id")
    front = _FRONT_MATTER.match(text)
    if front:
        match = _REVIEW_ID.search(front.group(0))
        if match:
            return validate_artifact_id(match.group("id"), source="artifact front matter")
    slug = normalize_slug(path.stem)[:MAX_ARTIFACT_ID_LENGTH]
    if not slug:
        raise ConfigError(f"cannot derive an artifact id from {path}; pass --artifact-id")
    return validate_artifact_id(slug, source=f"filename {path.name!r}")
