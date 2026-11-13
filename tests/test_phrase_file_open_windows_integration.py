"""Exercise phrase-file opens against native Windows filesystem handles."""

from __future__ import annotations

import ctypes
import os
import pathlib
import struct
import typing as typ

import pytest

from typos_config_builder import phrase_file_open_windows as windows
from typos_config_builder import phrase_file_open_windows_native as native
from typos_config_builder import phrase_scan, phrases_files

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows APIs")


def _read_opened_file(root: pathlib.Path, relative: pathlib.Path) -> bytes | None:
    """Read one descriptor returned by the native tracked-file opener."""
    descriptor = windows.open_windows_tracked_file(root, relative.parts)
    if descriptor is None:
        return None
    with os.fdopen(descriptor, "rb") as source:
        return source.read()


def _junction_reparse_buffer(target: pathlib.Path) -> ctypes.Array[ctypes.c_char]:
    """Build the native mount-point payload for one directory junction."""
    substitute_name = f"\\??\\{target.resolve()}".encode("utf-16-le")
    print_name = str(target.resolve()).encode("utf-16-le")
    path_buffer = substitute_name + b"\0\0" + print_name + b"\0\0"
    reparse_buffer = ctypes.create_string_buffer(16 + len(path_buffer))
    struct.pack_into(
        "<IHHHHHH",
        reparse_buffer,
        0,
        0xA0000003,
        8 + len(path_buffer),
        0,
        0,
        len(substitute_name),
        len(substitute_name) + 2,
        len(print_name),
    )
    ctypes.memmove(ctypes.addressof(reparse_buffer) + 16, path_buffer, len(path_buffer))
    return reparse_buffer


def _create_directory_junction(junction: pathlib.Path, target: pathlib.Path) -> None:
    """Create a native NTFS junction for a Windows-only integration check."""
    windows_ctypes = typ.cast("native._WindowsCtypes", ctypes)
    kernel32 = typ.cast(
        "typ.Any", windows_ctypes.WinDLL("kernel32", use_last_error=True)
    )
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    junction.mkdir()
    handle = kernel32.CreateFileW(
        str(junction),
        0x40000000,
        0x7,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    reparse_buffer = _junction_reparse_buffer(target)
    bytes_returned = ctypes.c_uint32()
    try:
        succeeded = kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            reparse_buffer,
            ctypes.sizeof(reparse_buffer),
            None,
            0,
            ctypes.byref(bytes_returned),
            None,
        )
        if not succeeded:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.parametrize("location", ["root-file", "nested-file"])
def test_native_windows_opens_regular_files(
    tmp_path: pathlib.Path, location: str
) -> None:
    """Regular files remain readable through native root-relative handles."""
    root = tmp_path / "repository"
    root.mkdir()
    relative = pathlib.Path("README.md")
    if location == "nested-file":
        relative = pathlib.Path("docs") / relative
        (root / "docs").mkdir()
    (root / relative).write_bytes(b"bounded phrase input")

    assert _read_opened_file(root, relative) == b"bounded phrase input", (
        "read regular files through native root-relative handles"
    )


def test_native_windows_rejects_file_symlink(
    tmp_path: pathlib.Path,
) -> None:
    """A file symlink is rejected before its target can be read."""
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside target")
    link = root / "README.md"
    link.symlink_to(outside)

    with pytest.raises(OSError, match="reparse point"):
        windows.open_windows_tracked_file(root, ("README.md",))


def test_native_windows_rejects_directory_junction(
    tmp_path: pathlib.Path,
) -> None:
    """A junction cannot redirect an intermediate path component."""
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "README.md").write_bytes(b"outside target")
    junction = root / "docs"
    _create_directory_junction(junction, outside)

    with pytest.raises(OSError, match="reparse point"):
        windows.open_windows_tracked_file(root, ("docs", "README.md"))


def test_native_windows_directory_replacement_fails_before_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final file replaced after eligibility fails before content is read."""
    root = (tmp_path / "repository").resolve()
    root.mkdir()
    relative = pathlib.Path("README.md")
    candidate = root / relative
    candidate.write_text("eligible text", encoding="utf-8")
    original_open = phrases_files.open_tracked_file
    original_read = os.read
    read_calls: list[int] = []

    def replace_before_open(
        selected_root: pathlib.Path, selected_path: pathlib.Path
    ) -> int | None:
        """Replace the candidate after eligibility, immediately before open."""
        candidate.unlink()
        candidate.mkdir()
        return original_open(selected_root, selected_path)

    def observe_read(file_descriptor: int, size: int) -> bytes:
        """Record any content read after the candidate becomes a directory."""
        read_calls.append(file_descriptor)
        return original_read(file_descriptor, size)

    monkeypatch.setattr(phrases_files, "open_tracked_file", replace_before_open)
    monkeypatch.setattr(os, "read", observe_read)

    with pytest.raises(phrases_files.PhraseScanError):
        phrase_scan.read_scannable_text(candidate, relative, root)

    assert not read_calls, "fail before reading from a directory replacement"
