"""Open tracked Windows phrase inputs one handle-relative component at a time."""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import typing as typ

from .phrase_file_open_windows_native import (
    _is_safe_component,
    _windows_api,
    _WindowsApi,
)

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["open_windows_tracked_file"]


def _validate_components(parts: tuple[str, ...]) -> None:
    """Reject empty, parent, or multi-part names before native traversal."""
    if not parts:
        raise OSError(errno.EINVAL, "tracked path must contain safe components")
    for part in parts:
        if not _is_safe_component(part):
            raise OSError(errno.EINVAL, "tracked path must contain safe components")


def _descriptor_is_safe(
    file_descriptor: int, root: pathlib.Path, windows: _WindowsApi
) -> bool:
    """Require the transferred descriptor to remain regular and contained."""
    if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
        return False
    if not windows.descriptor_path(file_descriptor).is_relative_to(root):
        raise OSError(errno.EACCES, "opened file escaped the worktree")
    return True


def _validate_root_handle(windows: _WindowsApi, root: pathlib.Path) -> int:
    """Open and validate the pinned worktree root handle."""
    handle = windows.open_root(root)
    with contextlib.ExitStack() as owned_handle:
        owned_handle.callback(windows.close_handle, handle)
        if windows.is_reparse_point(handle):
            raise OSError(errno.EACCES, "worktree root is a reparse point")
        if not windows.is_directory(handle):
            raise OSError(errno.ENOTDIR, "worktree root is not a directory")
        if windows.final_path(handle) != root:
            raise OSError(errno.EACCES, "worktree root changed before it was opened")
        owned_handle.pop_all()
    return handle


def _open_directory_component(
    windows: _WindowsApi,
    parent: int,
    component: str,
    root: pathlib.Path,
) -> int:
    """Open one contained directory and release it if validation fails."""
    with contextlib.ExitStack() as candidate:
        handle = windows.open_relative_no_reparse(parent, component, for_read=False)
        candidate.callback(windows.close_handle, handle)
        if windows.is_reparse_point(handle):
            raise OSError(errno.ELOOP, "tracked path contains a reparse point")
        if not windows.is_directory(handle):
            raise OSError(errno.ENOTDIR, "path component is not a directory")
        if not windows.final_path(handle).is_relative_to(root):
            raise OSError(errno.EACCES, "tracked path escaped the worktree")
        candidate.pop_all()
    return handle


def _open_final_file(
    windows: _WindowsApi,
    parent: int,
    component: str,
    root: pathlib.Path,
) -> int | None:
    """Transfer one contained regular-file handle into a Python descriptor."""
    handle = windows.open_relative_no_reparse(parent, component, for_read=True)
    with contextlib.ExitStack() as owned_handle:
        owned_handle.callback(windows.close_handle, handle)
        if windows.is_reparse_point(handle):
            raise OSError(errno.ELOOP, "tracked path contains a reparse point")
        if windows.is_directory(handle):
            raise OSError(errno.EISDIR, "tracked path was replaced by a directory")
        if not windows.is_regular_file(handle):
            return None
        if not windows.final_path(handle).is_relative_to(root):
            raise OSError(errno.EACCES, "opened file escaped the worktree")
        file_descriptor = windows.descriptor_from_handle(handle)
        owned_handle.pop_all()
    with contextlib.ExitStack() as owned_descriptor:
        owned_descriptor.callback(os.close, file_descriptor)
        if not _descriptor_is_safe(file_descriptor, root, windows):
            return None
        owned_descriptor.pop_all()
    return file_descriptor


def open_windows_tracked_file(root: pathlib.Path, parts: tuple[str, ...]) -> int | None:
    """Open a tracked file without traversing any reparse-point component.

    Each component is opened relative to a held directory with
    ``FILE_OPEN_REPARSE_POINT`` and checked before it becomes the next root.
    Held handles deny delete sharing, pinning directories during the walk.

    Parameters
    ----------
    root
        Resolved worktree that anchors the component walk.
    parts
        Repository-relative path components, each a single safe name.

    Returns
    -------
    int | None
        Open descriptor, or ``None`` if the final handle is not a regular file.

    Raises
    ------
    OSError
        For an unsafe component, a reparse point, an escaped path, a final
        directory, or a failed native operation.

    Examples
    --------
    >>> open_windows_tracked_file(  # doctest: +SKIP
    ...     pathlib.Path("C:/repo"), ("README.md",)
    ... )
    3
    """
    _validate_components(parts)
    windows = _windows_api()
    with contextlib.ExitStack() as handles:
        parent = _validate_root_handle(windows, root)
        handles.callback(windows.close_handle, parent)
        for component in parts[:-1]:
            parent = _open_directory_component(windows, parent, component, root)
            handles.callback(windows.close_handle, parent)
        return _open_final_file(windows, parent, parts[-1], root)
