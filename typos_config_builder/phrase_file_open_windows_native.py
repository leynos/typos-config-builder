"""Bind Windows APIs used by the phrase-file handle walker.

The rationale and alternatives are recorded in
``docs/adrs/0002-open-windows-phrase-inputs-with-relative-handles.md``.
"""

from __future__ import annotations

import ctypes
import errno
import os
import pathlib
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_OPEN = 1
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_READ_DATA = 0x1
_FILE_SHARE_READ, _FILE_SHARE_WRITE = 0x1, 0x2
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_TRAVERSE = 0x20
_FILE_TYPE_DISK = 1
_OBJ_CASE_INSENSITIVE = 0x40
_SYNCHRONIZE = 0x00100000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _UnicodeString(ctypes.Structure):
    """Match the variable-length Unicode name descriptor used by NT APIs."""

    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("MaximumLength", ctypes.c_uint16),
        ("Buffer", ctypes.c_wchar_p),
    ]


class _ObjectAttributes(ctypes.Structure):
    """Match the root-relative object attributes accepted by NtCreateFile."""

    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", ctypes.c_uint32),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IoStatusUnion(ctypes.Union):
    """Represent the status-or-pointer member of an NT I/O status block."""

    _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    """Receive the completion status from a native file open."""

    _fields_ = [("Status", _IoStatusUnion), ("Information", ctypes.c_size_t)]


class _FileAttributeTagInfo(ctypes.Structure):
    """Receive attributes used to distinguish files from reparse points."""

    _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]


class _WindowsProcedure(typ.Protocol):
    """Describe a configured Windows procedure callable through ctypes."""

    argtypes: object
    restype: object

    def __call__(self, *arguments: object) -> object:
        """Invoke the configured Windows procedure with native arguments."""
        ...


class _Kernel32(typ.Protocol):
    """Describe the kernel32 calls used to inspect and own file handles."""

    CreateFileW: _WindowsProcedure
    GetFileInformationByHandleEx: _WindowsProcedure
    GetFileType: _WindowsProcedure
    GetFinalPathNameByHandleW: _WindowsProcedure
    CloseHandle: _WindowsProcedure


class _Ntdll(typ.Protocol):
    """Describe the NT calls used for root-relative, no-reparse opens."""

    NtCreateFile: _WindowsProcedure
    RtlNtStatusToDosError: _WindowsProcedure


class _Msvcrt(typ.Protocol):
    """Describe the CRT conversions between native handles and descriptors."""

    open_osfhandle: cabc.Callable[[int, int], int]
    get_osfhandle: cabc.Callable[[int], int]


class _WindowsApi(typ.Protocol):
    """Provide the operations needed by the bounded Windows path walker."""

    def open_root(self, root: pathlib.Path) -> int:
        """Open the worktree directory without following a final reparse point."""
        ...

    def open_relative_no_reparse(
        self, parent: int, component: str, *, for_read: bool
    ) -> int:
        """Open one child relative to a held parent handle."""
        ...

    def is_reparse_point(self, handle: int) -> bool:
        """Report whether a handle names a reparse point."""
        ...

    def is_directory(self, handle: int) -> bool:
        """Report whether a handle names a directory."""
        ...

    def is_regular_file(self, handle: int) -> bool:
        """Report whether a handle names a regular disk file."""
        ...

    def final_path(self, handle: int) -> pathlib.Path:
        """Return the resolved path held by a native handle."""
        ...

    def descriptor_from_handle(self, handle: int) -> int:
        """Transfer a native file handle into a CRT descriptor."""
        ...

    def descriptor_path(self, file_descriptor: int) -> pathlib.Path:
        """Return the resolved path held by a CRT descriptor."""
        ...

    def close_handle(self, handle: int) -> None:
        """Close a native handle that has not transferred to a descriptor."""
        ...


class _NativeWindowsApi:
    """Bind the narrow native API surface used by the Windows reader."""

    def __init__(self, kernel32: _Kernel32, nt: _Ntdll, crt: _Msvcrt) -> None:
        """Bind native libraries and configure each called procedure."""
        self._kernel32 = kernel32
        self._ntdll = nt
        self._msvcrt = crt
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        """Set ctypes argument and result shapes before native calls."""
        handle = ctypes.c_void_p
        dword = ctypes.c_uint32
        pointer = ctypes.POINTER
        signatures = (
            (
                self._kernel32.CreateFileW,
                (ctypes.c_wchar_p, dword, dword, ctypes.c_void_p, dword, dword, handle),
                handle,
            ),
            (
                self._kernel32.GetFileInformationByHandleEx,
                (handle, ctypes.c_int, ctypes.c_void_p, dword),
                ctypes.c_int,
            ),
            (self._kernel32.GetFileType, (handle,), dword),
            (
                self._kernel32.GetFinalPathNameByHandleW,
                (handle, ctypes.c_wchar_p, dword, dword),
                dword,
            ),
            (self._kernel32.CloseHandle, (handle,), ctypes.c_int),
            (
                self._ntdll.NtCreateFile,
                (
                    pointer(handle),
                    dword,
                    pointer(_ObjectAttributes),
                    pointer(_IoStatusBlock),
                    ctypes.c_void_p,
                    dword,
                    dword,
                    dword,
                    dword,
                    ctypes.c_void_p,
                    dword,
                ),
                ctypes.c_int32,
            ),
            (self._ntdll.RtlNtStatusToDosError, (ctypes.c_int32,), dword),
        )
        for procedure, arguments, result in signatures:
            procedure.argtypes, procedure.restype = arguments, result

    @staticmethod
    def _raise_last_error() -> typ.NoReturn:
        """Convert the thread-local Win32 error into an ``OSError``."""
        windows_ctypes = typ.cast("_WindowsCtypes", ctypes)
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    def _raise_ntstatus(self, status: int) -> typ.NoReturn:
        """Convert an NT status into the matching Win32 ``OSError``."""
        windows_ctypes = typ.cast("_WindowsCtypes", ctypes)
        error_code = typ.cast("int", self._ntdll.RtlNtStatusToDosError(status))
        raise windows_ctypes.WinError(error_code)

    @staticmethod
    def _handle_value(handle: object) -> int:
        """Reject null and invalid handles returned by Win32."""
        match handle:
            case ctypes.c_void_p(value=int() as value) | (int() as value):
                if value not in {0, _INVALID_HANDLE_VALUE}:
                    return value
        raise OSError(errno.EBADF, "Windows returned an invalid file handle")

    def open_root(self, root: pathlib.Path) -> int:
        """Open the root directory with traversal access and no delete sharing."""
        access = _FILE_READ_ATTRIBUTES | _FILE_TRAVERSE | _SYNCHRONIZE
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
        flags = _FILE_FLAG_BACKUP_SEMANTICS | _FILE_OPEN_REPARSE_POINT
        handle = self._kernel32.CreateFileW(
            str(root), access, share, None, 3, flags, None
        )
        try:
            return self._handle_value(handle)
        except OSError:
            self._raise_last_error()

    def open_relative_no_reparse(
        self, parent: int, component: str, *, for_read: bool
    ) -> int:
        """Open one held-parent name with FILE_OPEN_REPARSE_POINT protection."""
        if not _is_safe_component(component):
            raise OSError(errno.EINVAL, "Windows relative open requires one component")
        status, opened_handle = self._issue_relative_open(
            parent, component, for_read=for_read
        )
        if status < 0:
            if opened_handle is not None:
                self.close_handle(opened_handle)
            self._raise_ntstatus(status)
        if opened_handle is None:
            raise OSError(errno.EIO, "NtCreateFile returned no handle")
        return opened_handle

    def _issue_relative_open(
        self, parent: int, component: str, *, for_read: bool
    ) -> tuple[int, int | None]:
        """Issue NtCreateFile for one name relative to its held parent."""
        name_buffer = ctypes.create_unicode_buffer(component)
        byte_length = len(component.encode("utf-16-le"))
        unicode_name = _UnicodeString(
            byte_length,
            byte_length + 2,
            ctypes.cast(name_buffer, ctypes.c_wchar_p),
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            ctypes.c_void_p(parent),
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        io_status = _IoStatusBlock()
        opened_handle = ctypes.c_void_p()
        access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        access |= _FILE_READ_DATA if for_read else _FILE_TRAVERSE
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
        options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
        call_arguments = (
            ctypes.byref(opened_handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            share,
            _FILE_OPEN,
            options,
            None,
            0,
        )
        return (
            typ.cast("int", self._ntdll.NtCreateFile(*call_arguments)),
            opened_handle.value,
        )

    def _attributes(self, handle: int) -> int:
        """Read the file-attribute flags associated with a handle."""
        information = _FileAttributeTagInfo()
        if not typ.cast(
            "bool",
            self._kernel32.GetFileInformationByHandleEx(
                handle,
                _FILE_ATTRIBUTE_TAG_INFO_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ),
        ):
            self._raise_last_error()
        return information.FileAttributes

    def is_reparse_point(self, handle: int) -> bool:
        """Identify symbolic links and junctions from handle attributes."""
        return bool(self._attributes(handle) & _FILE_ATTRIBUTE_REPARSE_POINT)

    def is_directory(self, handle: int) -> bool:
        """Identify disk-backed directory handles."""
        return bool(
            typ.cast("int", self._kernel32.GetFileType(handle)) == _FILE_TYPE_DISK
            and self._attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY
        )

    def is_regular_file(self, handle: int) -> bool:
        """Identify disk-backed files that are neither directories nor links."""
        attributes = self._attributes(handle)
        return bool(
            typ.cast("int", self._kernel32.GetFileType(handle)) == _FILE_TYPE_DISK
            and not attributes
            & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
        )

    def final_path(self, handle: int) -> pathlib.Path:
        """Return a handle's resolved path without the extended-path prefix."""
        buffer = ctypes.create_unicode_buffer(32768)
        length = typ.cast(
            "int",
            self._kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0),
        )
        if length == 0 or length >= len(buffer):
            self._raise_last_error()
        final_path = buffer.value
        if final_path.startswith("\\\\?\\UNC\\"):
            final_path = "\\\\" + final_path[8:]
        elif final_path.startswith("\\\\?\\"):
            final_path = final_path[4:]
        return pathlib.Path(final_path)

    def descriptor_from_handle(self, handle: int) -> int:
        """Transfer handle ownership to a binary-mode CRT descriptor."""
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        return self._msvcrt.open_osfhandle(handle, flags)

    def descriptor_path(self, file_descriptor: int) -> pathlib.Path:
        """Resolve the native handle owned by a CRT descriptor."""
        handle = self._msvcrt.get_osfhandle(file_descriptor)
        return self.final_path(handle)

    def close_handle(self, handle: int) -> None:
        """Close a native handle and surface any Win32 failure."""
        if not typ.cast("bool", self._kernel32.CloseHandle(handle)):
            self._raise_last_error()


class _WindowsCtypes(typ.Protocol):
    """Describe ctypes extensions that are available on Windows."""

    WinDLL: cabc.Callable[..., object]
    WinError: cabc.Callable[[int], OSError]
    get_last_error: cabc.Callable[[], int]


def _windows_api() -> _WindowsApi:
    """Load Windows libraries only when the platform adapter is selected."""
    import msvcrt

    windows_ctypes = typ.cast("_WindowsCtypes", ctypes)
    kernel32 = typ.cast(
        "_Kernel32", windows_ctypes.WinDLL("kernel32", use_last_error=True)
    )
    ntdll = typ.cast("_Ntdll", windows_ctypes.WinDLL("ntdll"))
    windows_msvcrt = typ.cast("_Msvcrt", msvcrt)
    return _NativeWindowsApi(kernel32, ntdll, windows_msvcrt)


def _is_safe_component(component: str) -> bool:
    """Return whether a path part contains exactly one safe name component."""
    if not component or component in {".", ".."}:
        return False
    if "/" in component:
        return False
    return "\\" not in component
