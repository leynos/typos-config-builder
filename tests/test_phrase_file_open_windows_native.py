"""Exercise native Windows API bindings with portable test doubles."""

from __future__ import annotations

import ctypes
import errno
import pathlib
import sys
import typing as typ
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from typos_config_builder import phrase_file_open
from typos_config_builder import phrase_file_open_windows as windows
from typos_config_builder import phrase_file_open_windows_native as native

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _native_test_callbacks(
    captures: list[tuple[int, str, int, int, int, int]],
    attributes_by_handle: dict[int, int],
    paths: dict[int, str],
) -> tuple[cabc.Callable[..., int], cabc.Callable[..., int], cabc.Callable[..., int]]:
    """Build callbacks that emulate the native handle queries."""

    def nt_create_file(
        output: object,
        access: int,
        attributes_pointer: object,
        *_arguments: object,
    ) -> int:
        """Capture the root, leaf, access, and create options passed to NT."""
        attributes = ctypes.cast(
            typ.cast("typ.Any", attributes_pointer),
            ctypes.POINTER(native._ObjectAttributes),
        ).contents
        component = attributes.ObjectName.contents.Buffer
        captures.append((
            attributes.RootDirectory,
            component,
            access,
            typ.cast("int", _arguments[3]),
            typ.cast("int", _arguments[4]),
            typ.cast("int", _arguments[5]),
        ))
        ctypes.cast(
            typ.cast("typ.Any", output), ctypes.POINTER(ctypes.c_void_p)
        ).contents.value = 22
        return 0

    def get_info(_handle: int, _kind: int, output: object, _size: int) -> int:
        """Return the test attributes for each native handle."""
        info = ctypes.cast(
            typ.cast("typ.Any", output), ctypes.POINTER(native._FileAttributeTagInfo)
        ).contents
        info.FileAttributes = attributes_by_handle[_handle]
        info.ReparseTag = 0
        return 1

    def get_path(handle: int, buffer: object, _size: int, _flags: int) -> int:
        """Copy a synthetic final path into the Win32 output buffer."""
        typ.cast("typ.Any", buffer).value = paths[handle]
        return len(paths[handle])

    return nt_create_file, get_info, get_path


def _native_api_with_captured_calls() -> tuple[
    native._NativeWindowsApi,
    list[tuple[int, str, int, int, int, int]],
    SimpleNamespace,
]:
    """Build a native API double that records one relative-open request."""
    captures: list[tuple[int, str, int, int, int, int]] = []
    attributes_by_handle = {
        11: native._FILE_ATTRIBUTE_DIRECTORY,
        22: native._FILE_ATTRIBUTE_DIRECTORY,
        23: native._FILE_ATTRIBUTE_REPARSE_POINT,
        24: 0,
    }
    paths = {
        11: "\\\\?\\C:\\repo",
        22: "\\\\?\\C:\\repo\\docs",
        23: "\\\\?\\C:\\repo\\link",
        24: "\\\\?\\C:\\repo\\README.md",
    }
    nt_create_file, get_info, get_path = _native_test_callbacks(
        captures, attributes_by_handle, paths
    )
    kernel32 = SimpleNamespace(
        CreateFileW=mock.Mock(return_value=11),
        GetFileInformationByHandleEx=mock.Mock(side_effect=get_info),
        GetFileType=mock.Mock(return_value=native._FILE_TYPE_DISK),
        GetFinalPathNameByHandleW=mock.Mock(side_effect=get_path),
        CloseHandle=mock.Mock(return_value=1),
    )
    ntdll = SimpleNamespace(
        NtCreateFile=mock.Mock(side_effect=nt_create_file),
        RtlNtStatusToDosError=mock.Mock(return_value=5),
    )
    msvcrt = SimpleNamespace(
        open_osfhandle=mock.Mock(return_value=31),
        get_osfhandle=mock.Mock(return_value=24),
    )
    return native._NativeWindowsApi(kernel32, ntdll, msvcrt), captures, kernel32


def test_native_component_call_is_single_name_and_opens_reparse_object(
    tmp_path: pathlib.Path,
) -> None:
    """A single-component native open anchors the name and disables reparse."""
    root = tmp_path / "repository"
    api, captures, kernel32 = _native_api_with_captured_calls()
    root_handle = api.open_root(root)
    child = api.open_relative_no_reparse(root_handle, "docs", for_read=False)

    assert root_handle == 11, "open the configured root directory"
    assert child == 22, "open the child relative to its parent handle"
    assert api.is_directory(11), "identify the repository handle as a directory"
    assert api.is_directory(22), "identify the nested handle as a directory"
    assert api.is_reparse_point(23), "identify a symbolic link reparse point"
    assert not api.is_regular_file(22), "do not classify a directory as a file"
    assert not api.is_regular_file(23), "do not classify a reparse point as a file"
    assert api.is_regular_file(24), "accept a regular disk file"
    assert str(api.final_path(11)) == "C:\\repo", "normalize the root handle path"
    assert api.final_path(22) == pathlib.Path("C:\\repo\\docs"), (
        "resolve a nested directory handle"
    )
    assert api.descriptor_path(31) == pathlib.Path("C:\\repo\\README.md"), (
        "resolve the path held by the transferred descriptor"
    )
    assert captures == [
        (
            11,
            "docs",
            native._FILE_READ_ATTRIBUTES | native._SYNCHRONIZE | native._FILE_TRAVERSE,
            native._FILE_SHARE_READ | native._FILE_SHARE_WRITE,
            native._FILE_OPEN,
            native._FILE_OPEN_REPARSE_POINT | native._FILE_SYNCHRONOUS_IO_NONALERT,
        )
    ], "issue a single-component no-reparse open relative to the held root"
    root_call = kernel32.CreateFileW.call_args.args
    assert root_call[0] == str(root), "open the requested root path"
    assert root_call[5] & native._FILE_OPEN_REPARSE_POINT, (
        "open the root without following a reparse point"
    )
    assert root_call[1] & native._FILE_TRAVERSE, "request traversal access for the root"
    assert not root_call[2] & 0x4, "deny delete sharing while the root is held"


def test_dispatcher_uses_windows_handle_relative_opener(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows dispatch cannot fall back to a path-based open."""
    root = tmp_path
    relative = pathlib.Path("docs/README.md")
    calls: list[tuple[pathlib.Path, tuple[str, ...]]] = []

    def open_windows(selected_root: pathlib.Path, parts: tuple[str, ...]) -> int:
        """Record the selected root and return the mocked descriptor."""
        calls.append((selected_root, parts))
        return 73

    monkeypatch.setattr(windows, "open_windows_tracked_file", open_windows)
    monkeypatch.setattr(phrase_file_open, "os", SimpleNamespace(name="nt"))

    assert phrase_file_open.open_tracked_file(root, relative) == 73, (
        "return the descriptor from the Windows adapter"
    )
    assert calls == [(root, ("docs", "README.md"))], (
        "dispatch the repository-relative path as single components"
    )


@pytest.mark.parametrize(
    "component",
    [
        pytest.param("", id="empty"),
        pytest.param(".", id="current"),
        pytest.param("..", id="parent"),
        pytest.param("docs/README.md", id="slash"),
        pytest.param("docs\\README.md", id="backslash"),
    ],
)
def test_native_component_validation_rejects_path_syntax(component: str) -> None:
    """The native open accepts one name, never a second path traversal."""
    api, _, _ = _native_api_with_captured_calls()

    with pytest.raises(OSError, match="one component"):
        api.open_relative_no_reparse(11, component, for_read=False)


@pytest.mark.parametrize(
    "handle",
    [
        pytest.param(0, id="null"),
        pytest.param(ctypes.c_void_p(), id="null-pointer"),
        pytest.param(ctypes.c_void_p(-1).value, id="invalid-pointer"),
        pytest.param(ctypes.c_void_p(-1), id="invalid-pointer-object"),
    ],
)
def test_invalid_native_handles_are_rejected(handle: object) -> None:
    """Null and invalid Windows handles cannot become file descriptors."""
    with pytest.raises(OSError, match="invalid file handle"):
        native._NativeWindowsApi._handle_value(handle)


@pytest.mark.parametrize(
    ("handle", "expected"),
    [
        pytest.param(22, 22, id="integer-handle"),
        pytest.param(ctypes.c_void_p(22), 22, id="pointer-handle"),
    ],
)
def test_valid_native_handles_are_normalized(handle: object, expected: int) -> None:
    """Native integer and pointer handles normalize to a descriptor value."""
    actual = native._NativeWindowsApi._handle_value(handle)

    assert actual == expected, "return the valid numeric handle value"


def test_open_root_translates_an_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid root handle becomes a useful operating-system error."""
    api, _, kernel32 = _native_api_with_captured_calls()
    kernel32.CreateFileW.return_value = ctypes.c_void_p(0)
    monkeypatch.setattr(
        api,
        "_raise_last_error",
        mock.Mock(side_effect=OSError(errno.EBADF, "invalid root handle")),
    )

    with pytest.raises(OSError, match="invalid root handle"):
        api.open_root(pathlib.Path("C:/repo"))


def test_partial_native_open_handle_is_closed_on_ntstatus_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An NT error that returns a handle still releases that partial handle."""
    api, _, kernel32 = _native_api_with_captured_calls()

    def fail_with_handle(output: object, *_arguments: object) -> int:
        """Return an NT error after populating the output handle."""
        ctypes.cast(
            typ.cast("typ.Any", output), ctypes.POINTER(ctypes.c_void_p)
        ).contents.value = 99
        return -1

    error_codes: list[int] = []

    def make_os_error(error_code: int) -> OSError:
        """Record the Win32 error code and provide a portable exception."""
        error_codes.append(error_code)
        return OSError(error_code, "translated Windows error")

    monkeypatch.setattr(native.ctypes, "WinError", make_os_error, raising=False)
    nt_create_file = typ.cast("typ.Any", api._ntdll.NtCreateFile)
    nt_create_file.side_effect = fail_with_handle

    with pytest.raises(OSError, match="translated Windows error"):
        api.open_relative_no_reparse(11, "docs", for_read=False)

    kernel32.CloseHandle.assert_called_once_with(99)
    assert error_codes == [5], "translate the native status to a Win32 error"


def test_native_open_rejects_success_without_a_handle() -> None:
    """A successful NT status without an output handle fails closed."""
    api, _, _ = _native_api_with_captured_calls()
    nt_create_file = typ.cast("typ.Any", api._ntdll.NtCreateFile)
    nt_create_file.side_effect = None
    nt_create_file.return_value = 0

    with pytest.raises(OSError, match="returned no handle"):
        api.open_relative_no_reparse(11, "docs", for_read=False)


def test_windows_errors_and_failed_handle_queries_are_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32 failures surface as OSError through each native error path."""
    api, _, kernel32 = _native_api_with_captured_calls()
    error_codes: list[int] = []

    def make_os_error(error_code: int) -> OSError:
        """Record the Win32 error code and provide a portable exception."""
        error_codes.append(error_code)
        return OSError(error_code, "translated Windows error")

    monkeypatch.setattr(native.ctypes, "WinError", make_os_error, raising=False)
    monkeypatch.setattr(native.ctypes, "get_last_error", lambda: 5, raising=False)

    with pytest.raises(OSError, match="translated Windows error"):
        api._raise_last_error()
    with pytest.raises(OSError, match="translated Windows error"):
        api._raise_ntstatus(-1)

    kernel32.GetFileInformationByHandleEx.side_effect = None
    kernel32.GetFileInformationByHandleEx.return_value = 0
    with pytest.raises(OSError, match="translated Windows error"):
        api.is_directory(11)
    kernel32.GetFileInformationByHandleEx.return_value = 1
    api.close_handle(11)
    kernel32.CloseHandle.return_value = 0
    with pytest.raises(OSError, match="translated Windows error"):
        api.close_handle(11)

    assert error_codes == [5, 5, 5, 5], "translate each native path failure"


def test_windows_path_normalization_handles_unc_paths_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final paths normalize UNC names and reject truncated API results."""
    api, _, kernel32 = _native_api_with_captured_calls()
    unc_path = "\\\\?\\UNC\\server\\share\\README.md"

    def provide_unc_path(_handle: int, buffer: object, _size: int, _flags: int) -> int:
        """Write a synthetic UNC path into the native output buffer."""
        typ.cast("typ.Any", buffer).value = unc_path
        return len(unc_path)

    kernel32.GetFinalPathNameByHandleW.side_effect = provide_unc_path
    assert api.final_path(11) == pathlib.Path("\\\\server\\share\\README.md"), (
        "normalize a UNC handle path"
    )

    monkeypatch.setattr(
        api,
        "_raise_last_error",
        mock.Mock(side_effect=OSError(errno.EIO, "short path")),
    )
    kernel32.GetFinalPathNameByHandleW.return_value = 0
    kernel32.GetFinalPathNameByHandleW.side_effect = None
    with pytest.raises(OSError, match="short path"):
        api.final_path(11)


def test_descriptor_transfer_and_platform_loader_bind_the_crt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows libraries and CRT descriptor conversion load only on dispatch."""
    api, _, kernel32 = _native_api_with_captured_calls()
    api.descriptor_from_handle(24)
    open_osfhandle = typ.cast("typ.Any", api._msvcrt.open_osfhandle)
    open_osfhandle.assert_called_once_with(
        24, native.os.O_RDONLY | getattr(native.os, "O_BINARY", 0)
    )

    crt = ModuleType("msvcrt")
    crt.__dict__.update({
        "open_osfhandle": mock.Mock(return_value=31),
        "get_osfhandle": mock.Mock(return_value=24),
    })
    monkeypatch.setitem(sys.modules, "msvcrt", crt)
    libraries = {"kernel32": kernel32, "ntdll": api._ntdll}
    loaded_names: list[str] = []

    def load_library(name: str, **_options: object) -> object:
        """Return a fake Windows library and record its lazy load."""
        loaded_names.append(name)
        return libraries[name]

    monkeypatch.setattr(native.ctypes, "WinDLL", load_library, raising=False)
    loaded_api = native._windows_api()

    assert isinstance(loaded_api, native._NativeWindowsApi), (
        "construct the native Windows API adapter"
    )
    assert loaded_names == ["kernel32", "ntdll"], "load only the required system DLLs"
