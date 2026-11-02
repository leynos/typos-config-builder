"""Open tracked phrase inputs without following an escaping path."""

from __future__ import annotations

import contextlib
import errno
import os
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["open_tracked_file"]


def _relative_parts(relative: pathlib.Path) -> tuple[str, ...]:
    """Validate repository-relative components before descriptor traversal."""
    parts = relative.parts
    if relative.is_absolute() or not parts:
        raise OSError(errno.EINVAL, "tracked path must be repository-relative")
    if any(part in {".", ".."} for part in parts):
        raise OSError(errno.EINVAL, "tracked path must stay within the worktree")
    return parts


def _open_posix_tracked_file(root: pathlib.Path, parts: tuple[str, ...]) -> int:
    """Open a regular-file candidate beneath no-follow directory descriptors."""
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if os.open not in os.supports_dir_fd or any(
        not hasattr(os, flag) for flag in required_flags
    ):
        raise OSError(errno.ENOTSUP, "safe directory-relative opening is unavailable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with contextlib.ExitStack() as stack:
        directory_fd = os.open(root, directory_flags)
        stack.callback(os.close, directory_fd)
        for component in parts[:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            stack.callback(os.close, directory_fd)
        return os.open(parts[-1], file_flags, dir_fd=directory_fd)


def open_tracked_file(root: pathlib.Path, relative: pathlib.Path) -> int | None:
    """Open a tracked file while keeping its eventual read inside ``root``.

    POSIX walks every component from the worktree descriptor and rejects
    symbolic links. Windows opens each component relative to a held directory
    handle with reparse processing disabled, then validates the final handle.

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
