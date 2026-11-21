"""Open tracked phrase inputs without following an escaping path.

Every component is opened relative to a directory descriptor with symbolic
links refused, so no path component can redirect the read during the walk.
That alone is not enough on POSIX: a held directory descriptor keeps working
after the directory it names moves elsewhere, so a directory renamed out of the
worktree mid-walk would still be traversed. The opened inode is therefore
re-verified against a fresh walk from the worktree root; a path that no longer
names the opened inode fails closed with ``ESTALE``.

Concurrent in-place modification of an already-contained file is outside the
guarantee: the bytes read are those of whichever version the descriptor names,
and a replacement is detected only when it changes the inode the tracked path
resolves to.
"""

from __future__ import annotations

import contextlib
import errno
import os
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["open_tracked_file"]

_REQUIRED_MODULE_FLAGS = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")

# Platform capability, resolved once because it cannot change at runtime. It is
# captured at import so a test double for ``os.open`` cannot look like a
# platform that lacks descriptor-relative opening.
_CAN_OPEN_SAFELY = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and all(hasattr(os, flag) for flag in _REQUIRED_MODULE_FLAGS)
)


def _relative_parts(relative: pathlib.Path) -> tuple[str, ...]:
    """Validate repository-relative components before descriptor traversal."""
    parts = relative.parts
    if relative.is_absolute() or not parts:
        raise OSError(errno.EINVAL, "tracked path must be repository-relative")
    if any(part in {".", ".."} for part in parts):
        raise OSError(errno.EINVAL, "tracked path must stay within the worktree")
    return parts


def _require_safe_open_facilities() -> None:
    """Refuse to walk a path when the platform cannot do it safely."""
    if not _CAN_OPEN_SAFELY:
        raise OSError(errno.ENOTSUP, "safe directory-relative opening is unavailable")


def _directory_flags() -> int:
    """Return the flags that open a real directory without following a link."""
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_directory_chain(
    stack: contextlib.ExitStack, root: pathlib.Path, parts: tuple[str, ...]
) -> int:
    """Open each component in turn, closing all but the last when done."""
    directory_fd = os.open(root, _directory_flags())
    stack.callback(os.close, directory_fd)
    for component in parts:
        directory_fd = os.open(component, _directory_flags(), dir_fd=directory_fd)
        stack.callback(os.close, directory_fd)
    return directory_fd


def _verify_still_contained(
    root: pathlib.Path, parts: tuple[str, ...], file_fd: int
) -> None:
    """Confirm the opened inode is still what the tracked path names.

    A held descriptor survives its directory being renamed out of the
    worktree, so the walk that opened the file proves nothing about where the
    file is now. Re-walking from the worktree root and comparing device and
    inode numbers refuses an input that has since escaped or been replaced.
    """
    opened = os.fstat(file_fd)
    try:
        with contextlib.ExitStack() as stack:
            parent_fd = _open_directory_chain(stack, root, parts[:-1])
            named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise OSError(
            errno.ESTALE,
            "tracked path left the worktree while it was being opened",
        ) from error
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError(
            errno.ESTALE,
            "tracked path was replaced while it was being opened",
        )


def _open_posix_tracked_file(root: pathlib.Path, parts: tuple[str, ...]) -> int:
    """Open a regular-file candidate beneath no-follow directory descriptors."""
    _require_safe_open_facilities()
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with contextlib.ExitStack() as stack:
        parent_fd = _open_directory_chain(stack, root, parts[:-1])
        file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        try:
            _verify_still_contained(root, parts, file_fd)
        except BaseException:
            os.close(file_fd)
            raise
        return file_fd


def open_tracked_file(root: pathlib.Path, relative: pathlib.Path) -> int | None:
    """Open a tracked file while keeping its eventual read inside ``root``.

    POSIX walks every component from the worktree descriptor, rejects symbolic
    links, and then re-verifies that the opened inode is still reachable at the
    tracked path, so a directory moved out of the worktree mid-walk is refused
    with ``ESTALE`` rather than read. Windows opens each component relative to
    a held directory handle with reparse processing disabled, then validates
    the final handle.

    Parameters
    ----------
    root
        Resolved worktree that anchors the open.
    relative
        Repository-relative tracked path.

    Returns
    -------
    int | None
        Open descriptor, or ``None`` if the final Windows handle is not a
        regular file. Unsupported safe-open facilities raise ``OSError``.

    Raises
    ------
    OSError
        If the path is not a safe repository-relative path, a component is a
        symbolic link, the platform lacks the facilities needed to open it
        safely, or the file left the worktree before it could be read.

    Examples
    --------
    >>> open_tracked_file(  # doctest: +SKIP
    ...     pathlib.Path("/repo"), pathlib.Path("README.md")
    ... )
    3
    """
    parts = _relative_parts(relative)
    if os.name == "nt":
        from typos_config_builder.phrase_file_open_windows import (
            open_windows_tracked_file,
        )

        return open_windows_tracked_file(root, parts)
    return _open_posix_tracked_file(root, parts)
