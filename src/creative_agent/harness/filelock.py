"""Advisory file locking and symlink-safe atomic writes.

Extracted from `harness/state.py` for two reasons. It was the single POSIX-only import in
`src/` (`fcntl` at module scope), which made `review` and `state show` unimportable off
POSIX; and the tmp-file and lock-file opens both followed symlinks, so a symlink planted at
either path turned an atomic state write into an arbitrary-file overwrite (DEC-F14).

Locking stays *advisory* and single-host: it serialises writers on one machine, which is
what `FileStateStore` needs, and is explicitly not a distributed lock. Correctness against
a concurrent writer comes from the optimistic cycle check in `FileStateStore.save`, not
from the lock — the lock only makes that check's read-and-write indivisible.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import ModuleType

# Platform locking primitives, bound once at import. `fcntl` is POSIX-only and was the
# single reason `harness/state.py` — and therefore `review` and `state show` — could not be
# imported on Windows at all. Binding both here (rather than importing inside the lock
# functions) keeps the dispatch a plain module lookup that a test can substitute, so the
# non-POSIX path is exercised rather than pragma'd out of the coverage gate.
_FCNTL: ModuleType | None
_MSVCRT: ModuleType | None
try:
    import fcntl as _fcntl_module
except ImportError:
    _FCNTL = None
else:
    _FCNTL = _fcntl_module
try:
    import msvcrt as _msvcrt_module
except ImportError:
    _MSVCRT = None
else:
    _MSVCRT = _msvcrt_module

# O_NOFOLLOW refuses to open a symlink at all, so a symlink planted at the lock path is an
# error instead of a write to wherever it points. It does not exist on every platform;
# where it is absent the flag is a no-op and the O_EXCL create below still protects the
# tmp file, which is the path that carries content.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# Lock files, temp files, and the review-state file they become. Least privilege: a
# rendered review report contains whatever the reviewed design contains.
_PRIVATE_FILE_MODE = 0o600
# msvcrt.locking works on a byte range rather than the whole file; one byte at offset 0 is
# the conventional whole-file stand-in.
_WINDOWS_LOCK_BYTES = 1


class FileLockUnavailableError(RuntimeError):
    """No advisory-locking primitive is available on this platform."""


def _lock_file(handle: int) -> None:
    """Take an exclusive advisory lock on an open descriptor, blocking until granted."""
    if _FCNTL is not None:
        _FCNTL.flock(handle, _FCNTL.LOCK_EX)
        return
    if _MSVCRT is not None:
        os.lseek(handle, 0, os.SEEK_SET)
        _MSVCRT.locking(handle, _MSVCRT.LK_LOCK, _WINDOWS_LOCK_BYTES)
        return
    raise FileLockUnavailableError(
        "no advisory file locking is available on this platform; review state cannot be "
        "written safely, so the review refuses to write rather than risk a lost update"
    )


def _unlock_file(handle: int) -> None:
    """Release the lock. Best-effort: the descriptor close below releases it regardless."""
    if _FCNTL is not None:
        _FCNTL.flock(handle, _FCNTL.LOCK_UN)
        return
    if _MSVCRT is not None:
        with suppress(OSError):
            os.lseek(handle, 0, os.SEEK_SET)
            _MSVCRT.locking(handle, _MSVCRT.LK_UNLCK, _WINDOWS_LOCK_BYTES)


@contextmanager
def exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for the duration of the block.

    The lock file persists between runs, so it cannot be created with `O_EXCL`; `O_NOFOLLOW`
    is what stops a symlink planted at that path from redirecting the open.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT | _O_NOFOLLOW, _PRIVATE_FILE_MODE)
    try:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)
    finally:
        os.close(handle)


def write_text_atomic(
    path: Path, content: str, *, tmp_suffix: str = ".tmp", mode: int = _PRIVATE_FILE_MODE
) -> None:
    """Write `content` to `path` atomically, following no symlink on the way.

    The temp file is unlinked first and then created with `O_EXCL`, so an existing file or
    a planted symlink at the temp path is replaced rather than written through — `unlink`
    removes a symlink itself, never its target. The content is fsynced before the rename so
    "atomic" survives a crash and not merely an interleaving, and the directory entry is
    fsynced afterwards on filesystems that support it.

    `os.replace` onto the final path is already safe: it replaces a symlink at that path
    rather than writing through it.

    The temp file's mode becomes the final file's mode, because `os.replace` keeps the
    source's. That is deliberate and 0600 is correct, but the comment on
    `_PRIVATE_FILE_MODE` used to call these files "process-private bookkeeping, not
    published artifacts" — which is false of this one: `docs/review-log/<id>.md` is a
    published artifact. Least privilege is still the right default for it, since a review
    report contains whatever the reviewed design contains, and git carries no non-exec
    mode, so committing it is unaffected. `mode` is a parameter rather than a constant so
    that a deployment publishing into a shared directory can widen it deliberately.
    """
    tmp_path = path.with_name(path.name + tmp_suffix)
    handle = open_exclusive_nofollow(tmp_path, mode=mode)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        # Never leave a partial temp file behind to be mistaken for state. This is a
        # `finally`, not `except OSError`: the original narrower form let a non-OS failure
        # mid-write (an encoding error, a bad value) leave the fragment on disk. After a
        # successful `os.replace` the temp path is already gone, so this is a no-op.
        tmp_path.unlink(missing_ok=True)
    with suppress(OSError):
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def open_exclusive_nofollow(path: Path, *, mode: int = _PRIVATE_FILE_MODE) -> int:
    """Create `path` fresh, refusing to follow or reuse anything already there.

    The unlink-then-`O_EXCL|O_NOFOLLOW` sequence is the whole symlink defence, and it is
    the one thing here that must not exist in two places: `unlink` removes a symlink
    itself rather than its target, and `O_EXCL` then guarantees the descriptor belongs to
    a file this call created. `write_text_atomic` opens its temp file through this rather
    than repeating the flags, so the two cannot drift.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, mode)
