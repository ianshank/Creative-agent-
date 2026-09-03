"""Artifact identity and loading (DEC-F7)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
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


def read_artifact(path: Path, max_bytes: int, *, containment_root: Path | None = None) -> str:
    """Read an artifact, refusing anything that is not a bounded regular file.

    Three refusals, all of which the reviewed repository can trigger. The documented
    `--artifact-repo` flow reviews a checked-out worktree, and git carries symlinks, so
    the artifact path is as untrusted as the artifact's contents (DEC-F9).

    - **Not a regular file.** `stat().st_size` reports 0 for a character device, so
      `docs/design.md` symlinked to `/dev/zero` passed the size cap and then read
      unbounded. A directory or a fifo would misbehave differently and just as usefully.
    - **A symlink out of the reviewed tree.** A path the operator located *inside*
      `containment_root` must still resolve inside it, so a symlink at `docs/design.md`
      pointing at `~/.ssh/id_rsa` is refused rather than read, delimited and handed to the
      model. The check is deliberately conditional on where the operator pointed: naming a
      document that lives outside the repository, while passing `--artifact-repo` so the
      repository's own decision log is what `DecisionGate` reads, is a legitimate pattern
      and is not what this defends against. Symlinks that stay inside the tree are fine —
      refusing every symlink would break ordinary checkouts to no benefit.
    - **Oversized**, as before, checked twice in case the file grows between the two calls.
    """
    try:
        info = path.stat()
    except OSError as exc:
        raise ConfigError(f"cannot read artifact {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(
            f"artifact {path} is not a regular file; a device, fifo or directory cannot be "
            "reviewed and its reported size is not its content length"
        )
    if containment_root is not None:
        try:
            root = containment_root.resolve()
            # abspath normalises `..` and the working directory without following symlinks:
            # this is where the operator SAID the file is, which is what decides whether
            # containment applies at all. `resolve()` below is where it actually is.
            declared = Path(os.path.abspath(path))
            if declared.is_relative_to(root) and not path.resolve().is_relative_to(root):
                raise ConfigError(
                    f"artifact {path} is inside the artifact repository {root} but resolves "
                    f"to {path.resolve()}, outside it; a symlink out of the reviewed tree is "
                    "not reviewable content"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(f"cannot resolve artifact {path}: {exc}") from exc
    if info.st_size > max_bytes:
        raise ConfigError(f"artifact {path} exceeds {max_bytes} bytes ({info.st_size} bytes)")
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
