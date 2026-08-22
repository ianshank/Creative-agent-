"""Artifact identity and loading (DEC-F7)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from creative_agent.errors import ConfigError
from creative_agent.models.findings import normalize_slug

_REVIEW_ID = re.compile(r"^review-id:\s*(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)\s*$", re.MULTILINE)
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def read_artifact(path: Path, max_bytes: int) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read artifact {path}: {exc}") from exc
    if len(data) > max_bytes:
        raise ConfigError(f"artifact {path} exceeds {max_bytes} bytes")
    return data.decode("utf-8", errors="replace")


def content_sha256(text: str) -> str:
    """Digest over LF-normalized bytes so CRLF checkouts don't change identity."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_artifact_id(path: Path, text: str, override: str | None) -> str:
    """--artifact-id flag > front-matter review-id > normalized filename slug."""
    if override:
        return override
    front = _FRONT_MATTER.match(text)
    if front:
        match = _REVIEW_ID.search(front.group(0))
        if match:
            return match.group("id")
    slug = normalize_slug(path.stem)
    if not slug:
        raise ConfigError(f"cannot derive an artifact id from {path}; pass --artifact-id")
    return slug
