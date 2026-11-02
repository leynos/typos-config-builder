"""Validate the opened Windows handle for a tracked phrase input."""

from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import stat
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = ["open_windows_tracked_file"]


class _WindowsProcedure(typ.Protocol):
    """Describe a ctypes procedure configured from the Windows API."""

    argtypes: object
    restype: object

    def __call__(self, handle: object, buffer: object, length: int, flags: int) -> int:
        """Call the Windows API procedure with its handle and output buffer."""
        ...


class _Kernel32(typ.Protocol):
    """Describe the one kernel32 procedure this adapter invokes."""

    GetFinalPathNameByHandleW: _WindowsProcedure


class _WindowsCtypes(typ.Protocol):
    """Describe ctypes extensions that are present only on Windows."""

    WinDLL: cabc.Callable[..., _Kernel32]
    WinError: cabc.Callable[[int], OSError]
    get_last_error: cabc.Callable[[], int]


class _WindowsMsvcrt(typ.Protocol):
    """Describe the Windows file-descriptor conversion used by msvcrt."""

    get_osfhandle: cabc.Callable[[int], int]


def _descriptor_path(file_descriptor: int) -> pathlib.Path:
    """Return the final path held by a Windows file descriptor."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    # Linux stubs omit these extension APIs, so describe their narrow surface.
    windows_ctypes = typ.cast("_WindowsCtypes", ctypes)
    windows_msvcrt = typ.cast("_WindowsMsvcrt", msvcrt)
    kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    handle = windows_msvcrt.get_osfhandle(file_descriptor)
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final_path(handle, buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    final_path = buffer.value
    if final_path.startswith("\\\\?\\UNC\\"):
        final_path = "\\\\" + final_path[8:]
    elif final_path.startswith("\\\\?\\"):
        final_path = final_path[4:]
    return pathlib.Path(final_path)


def _candidate_stat(candidate: pathlib.Path, root: pathlib.Path) -> os.stat_result:
    """Reject unsafe metadata before opening a Windows path."""
    before_open = os.lstat(candidate)
    if stat.S_ISLNK(before_open.st_mode) or not stat.S_ISREG(before_open.st_mode):
        raise OSError(errno.EINVAL, "tracked path changed before it could be opened")
    if not candidate.resolve().is_relative_to(root):
        raise OSError(errno.EACCES, "tracked path escaped the worktree")
    return before_open


def _descriptor_is_safe(
    file_descriptor: int, before_open: os.stat_result, root: pathlib.Path
) -> bool:
    """Validate file type, identity, and containment of the opened handle."""
    opened_stat = os.fstat(file_descriptor)
    if not stat.S_ISREG(opened_stat.st_mode):
        return False
    if (opened_stat.st_dev, opened_stat.st_ino) != (
        before_open.st_dev,
        before_open.st_ino,
    ):
        raise OSError(errno.EBUSY, "tracked path changed while it was opened")
    if not _descriptor_path(file_descriptor).is_relative_to(root):
        raise OSError(errno.EACCES, "opened file escaped the worktree")
    return True


def open_windows_tracked_file(root: pathlib.Path, parts: tuple[str, ...]) -> int | None:
    """Open and validate the final Windows handle before any content read.

    Parameters
    ----------
    root
        Resolved worktree against which the final handle is checked.
    parts
        Already-validated repository-relative path components.

    Returns
    -------
    int | None
        Open descriptor, or ``None`` if the opened handle is not a regular
        file.

    Examples
    --------
    >>> open_windows_tracked_file(  # doctest: +SKIP
    ...     pathlib.Path("C:/repo"), ("README.md",)
    ... )
    3
    """
    candidate = root.joinpath(*parts)
    before_open = _candidate_stat(candidate, root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    with contextlib.ExitStack() as stack:
        file_descriptor = os.open(candidate, flags)
        stack.callback(os.close, file_descriptor)
        if not _descriptor_is_safe(file_descriptor, before_open, root):
            return None
        stack.pop_all()
        return file_descriptor
