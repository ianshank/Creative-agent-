"""Advisory locking and symlink-safe atomic writes (DEC-F14).

The defects these guard were real: the state store's tmp-file write and its lock-file open
both followed symlinks, so a symlink planted at either path turned an atomic state write
into an arbitrary-file overwrite with partly attacker-influenced content.
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from creative_agent.harness import filelock
from creative_agent.harness.filelock import (
    FileLockUnavailableError,
    exclusive_lock,
    open_exclusive_nofollow,
    write_text_atomic,
)


class TestAtomicWriteRefusesSymlinks:
    def test_symlinked_temp_path_is_replaced_not_written_through(self, tmp_path: Path) -> None:
        """A symlink at <target>.tmp must not redirect the write.

        `write_text_atomic` creates the temp file, so a symlink planted there previously
        received the whole state document. `unlink` removes the link itself rather than
        its target, and `O_EXCL` then refuses to reuse anything that races back in.
        """
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched", encoding="utf-8")
        target = tmp_path / "state.md"
        (tmp_path / "state.md.tmp").symlink_to(victim)

        write_text_atomic(target, "new content")

        assert victim.read_text(encoding="utf-8") == "untouched"
        assert target.read_text(encoding="utf-8") == "new content"
        assert not (tmp_path / "state.md.tmp").exists()

    def test_symlinked_destination_is_replaced_not_written_through(self, tmp_path: Path) -> None:
        """os.replace onto the final path swaps the link, it does not follow it."""
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched", encoding="utf-8")
        target = tmp_path / "state.md"
        target.symlink_to(victim)

        write_text_atomic(target, "new content")

        assert victim.read_text(encoding="utf-8") == "untouched"
        assert not target.is_symlink()
        assert target.read_text(encoding="utf-8") == "new content"

    def test_content_is_lf_normalised_and_directory_created(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "state.md"
        write_text_atomic(target, "a\nb\n")
        assert target.read_bytes() == b"a\nb\n"

    def test_temp_file_is_removed_when_the_write_fails(self, tmp_path: Path) -> None:
        """A partial temp file must never be left behind to be mistaken for state."""
        target = tmp_path / "state.md"

        class Exploding:
            def __str__(self) -> str:
                raise OSError("disk full")

        with pytest.raises((OSError, TypeError)):
            write_text_atomic(target, Exploding())  # type: ignore[arg-type]
        assert not (tmp_path / "state.md.tmp").exists()

    def test_written_file_is_not_world_readable(self, tmp_path: Path) -> None:
        target = tmp_path / "state.md"
        write_text_atomic(target, "secretish")
        assert not os.stat(target).st_mode & 0o077


class TestExclusiveLock:
    def test_lock_is_reentrant_across_sequential_blocks(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".artifact.lock"
        for _ in range(3):
            with exclusive_lock(lock_path):
                pass
        assert lock_path.exists()

    def test_lock_path_symlink_is_refused(self, tmp_path: Path) -> None:
        """O_NOFOLLOW is the only thing protecting the lock path.

        Unlike the temp file the lock persists between runs, so it cannot be created with
        O_EXCL; without O_NOFOLLOW a symlink there is opened and truncated.
        """
        if not getattr(os, "O_NOFOLLOW", 0):
            pytest.skip("platform has no O_NOFOLLOW")
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched", encoding="utf-8")
        lock_path = tmp_path / ".artifact.lock"
        lock_path.symlink_to(victim)

        with pytest.raises(OSError), exclusive_lock(lock_path):
            pass
        assert victim.read_text(encoding="utf-8") == "untouched"

    def test_the_lock_is_released_when_the_body_raises(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".artifact.lock"
        with pytest.raises(RuntimeError), exclusive_lock(lock_path):
            raise RuntimeError("boom")
        with exclusive_lock(lock_path):
            pass


class TestPlatformBackendSelection:
    """The non-POSIX paths are substituted rather than pragma'd out of the gate.

    `fcntl` is POSIX-only and was the single import that made `review` and `state show`
    unimportable on Windows. Binding both backends at module scope keeps the dispatch a
    plain lookup, so these branches stay measurable on a Linux CI leg.
    """

    def test_windows_backend_is_used_when_fcntl_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, int]] = []
        fake = types.SimpleNamespace(
            LK_LOCK=1,
            LK_UNLCK=0,
            locking=lambda handle, mode, nbytes: calls.append(("locking", mode)),
        )
        monkeypatch.setattr(filelock, "_FCNTL", None)
        monkeypatch.setattr(filelock, "_MSVCRT", fake)

        with exclusive_lock(tmp_path / ".artifact.lock"):
            pass

        assert [mode for _, mode in calls] == [fake.LK_LOCK, fake.LK_UNLCK]

    def test_no_backend_refuses_to_write_rather_than_racing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silently skipping the lock would trade a loud failure for a lost update."""
        monkeypatch.setattr(filelock, "_FCNTL", None)
        monkeypatch.setattr(filelock, "_MSVCRT", None)
        with pytest.raises(FileLockUnavailableError), exclusive_lock(tmp_path / ".artifact.lock"):
            pass


def test_open_exclusive_nofollow_replaces_a_planted_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    target = tmp_path / "fresh"
    target.symlink_to(victim)

    handle = open_exclusive_nofollow(target)
    try:
        os.write(handle, b"new")
    finally:
        os.close(handle)

    assert victim.read_text(encoding="utf-8") == "untouched"
    assert target.read_bytes() == b"new"
