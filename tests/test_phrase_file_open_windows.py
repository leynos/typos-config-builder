"""Exercise the Windows phrase-file opener with Linux-safe native API doubles."""

from __future__ import annotations

import errno
import os
import typing as typ

import pytest

from typos_config_builder import phrase_file_open_windows as windows

if typ.TYPE_CHECKING:
    import pathlib


class _FakeWindowsApi:
    """Model handle-relative opens and their ownership for focused tests."""

    def __init__(
        self,
        root: pathlib.Path,
        entries: dict[tuple[str, ...], tuple[pathlib.Path, str, bool]],
        descriptor_file: pathlib.Path | None,
        *,
        fail_at: tuple[str, ...] | None = None,
    ) -> None:
        """Keep the fake tree and all handle-ownership observations."""
        self.root = root
        self.entries = entries
        self.descriptor_file = descriptor_file
        self.fail_at = fail_at
        self.handles: dict[int, tuple[tuple[str, ...], pathlib.Path, str, bool]] = {
            1: ((), root, "directory", False)
        }
        self.next_handle = 2
        self.opened: list[tuple[int, str, bool]] = []
        self.closed: list[int] = []
        self.converted: list[int] = []
        self.descriptor_paths: dict[int, pathlib.Path] = {}
        self.descriptor_path_override: pathlib.Path | None = None

    def open_root(self, root: pathlib.Path) -> int:
        """Return the synthetic handle for the repository root."""
        assert root == self.root, "open the configured repository root"
        return 1

    def open_relative_no_reparse(
        self, parent: int, component: str, *, for_read: bool
    ) -> int:
        """Record a child open and return its configured synthetic handle."""
        parent_parts = self.handles[parent][0]
        parts = (*parent_parts, component)
        self.opened.append((parent, component, for_read))
        if parts == self.fail_at:
            raise OSError(errno.EIO, "simulated component-open failure")
        path, kind, reparse = self.entries[parts]
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = (parts, path, kind, reparse)
        return handle

    def is_reparse_point(self, handle: int) -> bool:
        """Report whether the synthetic handle names a reparse point."""
        return self.handles[handle][3]

    def is_directory(self, handle: int) -> bool:
        """Report whether the synthetic handle names a directory."""
        return self.handles[handle][2] == "directory"

    def is_regular_file(self, handle: int) -> bool:
        """Report whether the synthetic handle names a regular file."""
        return self.handles[handle][2] == "file" and not self.is_reparse_point(handle)

    def final_path(self, handle: int) -> pathlib.Path:
        """Return the configured resolved path for a synthetic handle."""
        return self.handles[handle][1]

    def descriptor_from_handle(self, handle: int) -> int:
        """Transfer a synthetic file handle into a real test descriptor."""
        self.converted.append(handle)
        if self.descriptor_file is None:
            raise OSError(errno.EIO, "simulated descriptor conversion failure")
        descriptor = os.open(self.descriptor_file, os.O_RDONLY)
        self.descriptor_paths[descriptor] = self.final_path(handle)
        return descriptor

    def descriptor_path(self, file_descriptor: int) -> pathlib.Path:
        """Return the configured final path for a converted descriptor."""
        if self.descriptor_path_override is not None:
            return self.descriptor_path_override
        return self.descriptor_paths[file_descriptor]

    def close_handle(self, handle: int) -> None:
        """Record closure without touching the host process handle table."""
        self.closed.append(handle)


def _install_fake(monkeypatch: pytest.MonkeyPatch, api: _FakeWindowsApi) -> None:
    """Route the platform adapter through a deterministic handle model."""
    monkeypatch.setattr(windows, "_windows_api", lambda: api)


@pytest.mark.parametrize(
    "parts",
    [
        pytest.param((), id="empty"),
        pytest.param(("..",), id="parent"),
        pytest.param(("docs/README.md",), id="multiple-components"),
    ],
)
def test_invalid_components_are_rejected_before_opening(
    tmp_path: pathlib.Path, parts: tuple[str, ...]
) -> None:
    """Unsafe relative names cannot reach the native path walker."""
    with pytest.raises(OSError, match="safe components"):
        windows.open_windows_tracked_file(tmp_path, parts)


@pytest.mark.parametrize(
    "invalid_root",
    [
        pytest.param("reparse", id="reparse-root"),
        pytest.param("not-directory", id="file-root"),
        pytest.param("moved", id="changed-root"),
    ],
)
def test_invalid_root_handles_are_closed(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_root: str,
) -> None:
    """A changed or mis-typed repository root is refused and released."""
    root = tmp_path / "repository"
    api = _FakeWindowsApi(root, {}, None)
    if invalid_root == "reparse":
        monkeypatch.setattr(api, "is_reparse_point", lambda _handle: True)
        message = "reparse point"
    elif invalid_root == "not-directory":
        monkeypatch.setattr(api, "is_directory", lambda _handle: False)
        message = "not a directory"
    else:
        monkeypatch.setattr(api, "final_path", lambda _handle: root / "moved")
        message = "changed before"
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match=message):
        windows.open_windows_tracked_file(root, ("README.md",))

    assert api.closed == [1], "close the invalid repository root handle"


@pytest.mark.parametrize(
    "component_case",
    [
        pytest.param(("inside", "file", "not a directory"), id="not-directory"),
        pytest.param(("outside", "directory", "escaped the worktree"), id="outside"),
    ],
)
def test_directory_components_must_be_directories_inside_the_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    component_case: tuple[str, str, str],
) -> None:
    """Intermediate handles cannot redirect a walk outside the repository."""
    entry_path, entry_kind, message = component_case
    root = tmp_path / "repository"
    candidate = root / "docs" if entry_path == "inside" else tmp_path / "outside"
    api = _FakeWindowsApi(
        root,
        {("docs",): (candidate, entry_kind, False)},
        None,
    )
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match=message):
        windows.open_windows_tracked_file(root, ("docs", "README.md"))

    assert api.closed == [2, 1], "close the candidate directory before the root"


def test_ordinary_components_return_a_readable_descriptor(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regular file reached through held directories remains readable."""
    root = tmp_path / "repository"
    nested = root / "docs"
    nested.mkdir(parents=True)
    content = nested / "README.md"
    content.write_bytes(b"ordinary text")
    api = _FakeWindowsApi(
        root,
        {
            ("docs",): (nested, "directory", False),
            ("docs", "README.md"): (content, "file", False),
        },
        content,
    )
    _install_fake(monkeypatch, api)

    descriptor = windows.open_windows_tracked_file(root, ("docs", "README.md"))

    assert descriptor is not None, "return a descriptor for a regular tracked file"
    with os.fdopen(descriptor, "rb") as source:
        assert source.read() == b"ordinary text", "read the tracked file contents"
    assert api.opened == [(1, "docs", False), (2, "README.md", True)], (
        "open each path component relative to its parent handle"
    )
    assert api.converted == [3], "transfer the final file handle to a descriptor"
    assert api.closed == [2, 1], "close held directories after opening the file"


def test_reparse_component_is_refused_before_the_next_open(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction handle is rejected before a child name can be traversed."""
    root = tmp_path / "repository"
    root.mkdir()
    junction = root / "junction"
    api = _FakeWindowsApi(
        root,
        {
            ("junction",): (junction, "directory", True),
            ("junction", "README.md"): (junction / "README.md", "file", False),
        },
        root / "unused",
    )
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match="reparse point"):
        windows.open_windows_tracked_file(root, ("junction", "README.md"))

    assert api.opened == [(1, "junction", False)], "stop at the junction component"
    assert not api.converted, "never transfer a junction handle"
    assert api.closed == [2, 1], "close the rejected junction and repository root"


@pytest.mark.parametrize(
    "path_case",
    [pytest.param("outside-target"), pytest.param("replacement")],
)
def test_outside_target_or_replacement_never_yields_a_descriptor(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path_case: str,
) -> None:
    """An outside target or replacement cannot reach the phrase content read."""
    root = tmp_path / "repository"
    root.mkdir()
    content = root / "README.md"
    content.write_bytes(b"ordinary text")
    outside = tmp_path / "outside.md"
    final_path = content if path_case == "replacement" else outside
    api = _FakeWindowsApi(
        root,
        {("README.md",): (final_path, "file", False)},
        content,
    )
    if path_case == "replacement":
        api.descriptor_path_override = outside
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match="escaped the worktree"):
        windows.open_windows_tracked_file(root, ("README.md",))

    assert api.converted == ([2] if path_case == "replacement" else []), (
        "transfer a descriptor only after containment validation"
    )
    assert api.closed == ([1] if path_case == "replacement" else [2, 1]), (
        "release handles that were not transferred"
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("component-open", id="component-open"),
        pytest.param("descriptor-transfer", id="descriptor-transfer"),
    ],
)
def test_open_failures_release_owned_handles(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A failed component open or descriptor transfer closes every owned handle."""
    root = tmp_path / "repository"
    nested = root / "docs"
    nested.mkdir(parents=True)
    content = nested / "README.md"
    content.write_bytes(b"ordinary text")
    api = _FakeWindowsApi(
        root,
        {
            ("docs",): (nested, "directory", False),
            ("docs", "README.md"): (content, "file", False),
        },
        None if failure == "descriptor-transfer" else content,
        fail_at=("docs", "README.md") if failure == "component-open" else None,
    )
    _install_fake(monkeypatch, api)

    error_message, expected_closed = {
        "component-open": ("simulated component-open failure", [2, 1]),
        "descriptor-transfer": ("simulated descriptor conversion failure", [3, 2, 1]),
    }[failure]
    with pytest.raises(OSError, match=error_message):
        windows.open_windows_tracked_file(root, ("docs", "README.md"))

    assert api.closed == expected_closed, "release handles after a native failure"


def test_nonregular_final_handle_is_skipped_and_closed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A device-like final handle is skipped without transferring ownership."""
    root = tmp_path / "repository"
    root.mkdir()
    device = root / "NUL"
    api = _FakeWindowsApi(
        root,
        {("NUL",): (device, "device", False)},
        device,
    )
    _install_fake(monkeypatch, api)

    assert windows.open_windows_tracked_file(root, ("NUL",)) is None, (
        "skip a non-regular device handle"
    )
    assert not api.converted, "do not transfer a device handle"
    assert api.closed == [2, 1], "close the device and repository handles"


def test_final_reparse_handle_is_refused_before_descriptor_transfer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final symbolic link cannot transfer a handle to the phrase reader."""
    root = tmp_path / "repository"
    link = root / "README.md"
    api = _FakeWindowsApi(root, {("README.md",): (link, "file", True)}, link)
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match="reparse point"):
        windows.open_windows_tracked_file(root, ("README.md",))

    assert not api.converted, "do not transfer a reparse-point handle"
    assert api.closed == [2, 1], "close the link and repository handles"


def test_final_directory_handle_raises_and_is_closed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final directory fails closed instead of being skipped as a device."""
    root = tmp_path / "repository"
    directory = root / "README.md"
    api = _FakeWindowsApi(
        root, {("README.md",): (directory, "directory", False)}, directory
    )
    _install_fake(monkeypatch, api)

    with pytest.raises(OSError, match="replaced by a directory"):
        windows.open_windows_tracked_file(root, ("README.md",))

    assert not api.converted, "reject the directory before descriptor transfer"
    assert api.closed == [2, 1], "close the directory and repository handles"


def test_transferred_directory_descriptor_is_closed_and_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descriptor that changes type before validation is never returned."""
    root = tmp_path / "repository"
    target = root / "README.md"
    target.mkdir(parents=True)
    api = _FakeWindowsApi(root, {("README.md",): (target, "file", False)}, target)
    _install_fake(monkeypatch, api)

    assert windows.open_windows_tracked_file(root, ("README.md",)) is None, (
        "skip a descriptor found to be non-regular after transfer"
    )
    assert api.converted == [2], "verify the transferred descriptor type"
    assert api.closed == [1], "close the repository handle after the check"
