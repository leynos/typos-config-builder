"""Contracts for containment when opening a tracked phrase input on POSIX.

The walk refuses symbolic links, but a held directory descriptor keeps working
after its directory moves, so these tests move the parent out of the worktree
during the open itself. Each one proves the escape is refused before any byte
of the moved file is read, and that the refused open leaks no descriptor.
"""

from __future__ import annotations

import errno
import os
import pathlib
import typing as typ
from unittest import mock

import pytest

from typos_config_builder import phrase_file_open, phrases_files
from typos_config_builder.phrase_scan_results import ScanFailure

if typ.TYPE_CHECKING:
    import collections.abc as cabc

pytestmark = pytest.mark.skipif(
    os.name == "nt"
    or os.open not in os.supports_dir_fd
    or os.stat not in os.supports_dir_fd
    or os.stat not in os.supports_follow_symlinks
    or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX no-follow directory-relative opening",
)

RELATIVE = pathlib.Path("nested/README.md")


class _Repository:
    """Hold a worktree, the place its parent moves to, and descriptor records."""

    def __init__(self, root: pathlib.Path) -> None:
        """Create a worktree with one nested tracked file."""
        self.root = root / "repository"
        self.nested = self.root / "nested"
        self.nested.mkdir(parents=True)
        self.tracked = self.nested / "README.md"
        self.tracked.write_text("Tracked text.\n", encoding="utf-8")
        self.outside = root / "outside"
        self.outside.mkdir()
        self.opened: list[int] = []
        self.closed: set[int] = set()

    @property
    def resolved_root(self) -> pathlib.Path:
        """The resolved worktree anchor the open uses."""
        return self.root.resolve()

    def move_parent(self, *, replace: bool) -> None:
        """Move the parent out of the worktree, optionally leaving a lookalike."""
        self.nested.rename(self.outside / "nested")
        if not replace:
            return
        self.nested.mkdir()
        (self.nested / "README.md").write_text("External text.\n", encoding="utf-8")

    def audit(self) -> tuple[list[int], set[int]]:
        """Return the descriptors opened and closed during the test window."""
        return self.opened, self.closed


def _opening_after_move(
    repository: _Repository, *, replace: bool
) -> cabc.Callable[..., int]:
    """Return an ``os.open`` double that moves the parent before the file open.

    The double fires on the one call that opens the final component relative
    to a held directory descriptor, which is exactly the window in which a
    directory can be renamed out of the worktree after it was validated.
    """
    original_open = os.open

    def open_double(
        path: str | bytes,
        flags: int,
        *,
        dir_fd: int | None = None,
        **_ignored: object,
    ) -> int:
        """Move the parent, then open the requested component as usual."""
        if dir_fd is not None and not flags & os.O_DIRECTORY:
            repository.move_parent(replace=replace)
        file_descriptor = original_open(path, flags, dir_fd=dir_fd)
        repository.opened.append(file_descriptor)
        return file_descriptor

    return open_double


def _record_close(repository: _Repository) -> cabc.Callable[[int], None]:
    """Return an ``os.close`` double that records each closed descriptor."""
    original_close = os.close

    def close_double(file_descriptor: int) -> None:
        """Close the descriptor and remember that it was closed."""
        repository.closed.add(file_descriptor)
        original_close(file_descriptor)

    return close_double


@pytest.mark.parametrize("replace", [False, True], ids=["vanished", "lookalike"])
def test_directory_moved_out_of_the_worktree_is_refused(
    tmp_path: pathlib.Path, *, replace: bool
) -> None:
    """A parent moved out mid-open fails closed before the file is read."""
    repository = _Repository(tmp_path)
    read_calls: list[int] = []
    original_read = os.read

    def record_read(file_descriptor: int, size: int) -> bytes:
        """Record any read that reaches the moved file."""
        read_calls.append(file_descriptor)
        return original_read(file_descriptor, size)

    with (
        mock.patch.object(os, "open", _opening_after_move(repository, replace=replace)),
        mock.patch.object(os, "close", _record_close(repository)),
        mock.patch.object(os, "read", record_read),
        pytest.raises(phrases_files.PhraseScanError) as error,
    ):
        phrases_files.read_tracked_text(repository.resolved_root, RELATIVE)

    assert error.value.outcome is ScanFailure.READ, "report a fail-closed read"
    cause = error.value.__cause__
    assert isinstance(cause, OSError), "retain the containment failure"
    assert cause.errno == errno.ESTALE, "report the input as no longer at its path"
    assert not read_calls, "no content may be read from the moved directory"
    opened, closed = repository.audit()
    assert opened, "the test must have opened descriptors to audit"
    assert set(opened) <= closed, "a refused open must not leak a descriptor"


def test_open_refuses_a_platform_without_safe_facilities(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform lacking the safe-open primitives fails closed, never guesses."""
    repository = _Repository(tmp_path)
    monkeypatch.setattr(phrase_file_open, "_CAN_OPEN_SAFELY", False)

    with pytest.raises(OSError, match="safe directory-relative opening") as error:
        phrase_file_open.open_tracked_file(repository.resolved_root, RELATIVE)

    assert error.value.errno == errno.ENOTSUP, "refuse rather than open unsafely"
