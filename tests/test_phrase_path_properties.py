"""Property contracts for the phrase-input path walkers."""

from __future__ import annotations

import dataclasses as dc
import errno
import os
import pathlib
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from typos_config_builder import phrase_file_open, phrase_file_open_windows

SAFE_NAME = st.from_regex(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,15}", fullmatch=True)
SAFE_PARTS = st.lists(SAFE_NAME, min_size=1, max_size=4)
# ``pathlib`` drops ``"."``, leaving a parent component as the only unsafe name.
UNSAFE_COMPONENT = st.just("..")
# Every value the Windows opener must refuse before it consults the native API.
UNSAFE_COMPONENTS = st.sampled_from(["", ".", "..", "a/b", "a\\b"])

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt"
    or os.open not in os.supports_dir_fd
    or os.stat not in os.supports_dir_fd
    or os.stat not in os.supports_follow_symlinks
    or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX no-follow directory-relative opening",
)


@settings(max_examples=50, deadline=None)
@given(SAFE_PARTS)
def test_generated_safe_components_survive_validation(parts: list[str]) -> None:
    """Validation returns exactly the components a safe path already has."""
    relative = pathlib.Path(*parts)

    assert phrase_file_open._relative_parts(relative) == relative.parts


@settings(max_examples=50, deadline=None)
@given(SAFE_PARTS, UNSAFE_COMPONENT)
def test_generated_parent_components_are_rejected_at_any_depth(
    parts: list[str], unsafe: str
) -> None:
    """A parent or current-directory component is refused wherever it appears."""
    relative = pathlib.Path(*parts, unsafe, "tail.md")

    with pytest.raises(OSError, match="must stay within the worktree") as error:
        phrase_file_open._relative_parts(relative)

    assert error.value.errno == errno.EINVAL


@settings(max_examples=50, deadline=None)
@given(SAFE_PARTS)
def test_absolute_paths_are_rejected_at_any_depth(parts: list[str]) -> None:
    """No absolute path is ever accepted as a tracked component list."""
    # ``Path("/")`` has no drive on Windows, so anchor on the cwd instead.
    relative = pathlib.Path(pathlib.Path.cwd().anchor, *parts)

    with pytest.raises(OSError, match="must be repository-relative") as error:
        phrase_file_open._relative_parts(relative)

    assert error.value.errno == errno.EINVAL


class _RecordedOpen:
    """Record the arguments of every ``os.open`` the walker issues."""

    def __init__(self) -> None:
        """Keep the original callable and one record per invocation."""
        self.original = os.open
        self.calls: list[tuple[str, int, int | None]] = []

    def __call__(
        self, path: str | bytes, flags: int, *, dir_fd: int | None = None, **_: object
    ) -> int:
        """Record the call, then open exactly as the walker asked."""
        self.calls.append((str(path), flags, dir_fd))
        return self.original(path, flags, dir_fd=dir_fd)

    @property
    def components(self) -> list[str]:
        """The components opened relative to a held descriptor."""
        return [path for path, _, dir_fd in self.calls if dir_fd is not None]


def _build_tree(
    base: pathlib.Path, parts: list[str]
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a real nested worktree holding ``parts`` and return its anchors."""
    root = base / "repository"
    directory = root
    for component in parts[:-1]:
        directory /= component
    directory.mkdir(parents=True)
    tracked = directory / parts[-1]
    tracked.write_text("Tracked text.\n", encoding="utf-8")
    return root.resolve(), tracked


@POSIX_ONLY
@settings(max_examples=25, deadline=None)
@given(SAFE_PARTS)
def test_posix_walk_opens_only_the_tracked_components_in_order(
    tmp_path_factory: pytest.TempPathFactory,
    parts: list[str],
) -> None:
    """A walk cannot traverse a component the tracked path never named."""
    recorder = _RecordedOpen()
    root, tracked = _build_tree(tmp_path_factory.mktemp("phrase-path"), parts)

    with mock.patch.object(os, "open", recorder):
        file_descriptor = phrase_file_open.open_tracked_file(root, pathlib.Path(*parts))

    assert file_descriptor is not None, "a regular tracked file must be returned"
    try:
        opened = os.fstat(file_descriptor)
        expected = tracked.stat()
        assert (opened.st_dev, opened.st_ino) == (expected.st_dev, expected.st_ino), (
            "the descriptor must name the tracked file"
        )
    finally:
        os.close(file_descriptor)

    components = recorder.components
    assert set(components) <= set(parts), "never open a component outside the path"
    assert components[: len(parts)] == parts, "open the tracked components in order"


@POSIX_ONLY
def test_posix_opens_refuse_symbolic_links(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every open the walk issues asks for no-follow semantics."""
    recorder = _RecordedOpen()
    monkeypatch.setattr(os, "open", recorder)
    parts = ["docs", "nested", "README.md"]
    root, _ = _build_tree(tmp_path / "example", parts)

    file_descriptor = phrase_file_open.open_tracked_file(root, pathlib.Path(*parts))
    assert file_descriptor is not None
    os.close(file_descriptor)

    flags = [recorded for _, recorded, _ in recorder.calls]
    assert flags, "the walk must open at least the worktree root"
    assert all(flag & os.O_NOFOLLOW for flag in flags), (
        "every component open must refuse symbolic links"
    )
    assert any(flag & os.O_DIRECTORY for flag in flags), (
        "the walk must pin its directories"
    )


@dc.dataclass(frozen=True, slots=True)
class _WindowsCase:
    """Pair a generated tracked path with the failure its walk must meet."""

    parts: list[str]
    component: tuple[int, str] | None = None
    open_error: tuple[int, int] | None = None


class _FakeWindowsApi:
    """Model handle-relative traversal and record handle ownership."""

    def __init__(
        self, root: pathlib.Path, case: _WindowsCase, descriptor_file: pathlib.Path
    ) -> None:
        """Record the generated path, the modelled failure, and the file to open."""
        self.root = root
        self.parts = case.parts
        self.component = case.component
        self.descriptor_file = descriptor_file
        self.opened: list[str] = []
        self.closed: list[int] = []
        self.root_opens = 0
        self.next_handle = 2
        self.transferred = False
        self.transfers = 0
        self.open_error: OSError | None = None
        self.open_error_index: int | None = None
        if case.open_error is not None:
            self.open_error_index, error_number = case.open_error
            self.open_error = OSError(error_number, "injected open failure")

    @property
    def expected_closed(self) -> list[int]:
        """Every acquired handle, excluding any transferred handle."""
        transferred = self.next_handle - 1 if self.transferred else None
        return [h for h in range(1, self.next_handle) if h != transferred]

    def _kind(self, prefix: tuple[str, ...]) -> tuple[pathlib.Path, str, bool]:
        """Return the modelled path, entry type, and reparse flag for a prefix."""
        index = len(prefix) - 1
        is_final = index == len(self.parts) - 1
        path = self.root.joinpath(*prefix)
        if self.component is None or self.component[0] != index:
            return path, "file" if is_final else "directory", False
        kind = self.component[1]
        if kind == "reparse":
            return path, "directory", True
        if kind == "file":
            return path, "file", False
        if kind == "directory":
            return path, "directory", False
        outside = self.root.parent / "outside" / path.name
        return outside, "file" if is_final else "directory", False

    def _prefix(self, handle: int) -> tuple[str, ...]:
        """Return the modelled path prefix a synthetic handle names."""
        return () if handle == 1 else tuple(self.parts[: handle - 1])

    def open_root(self, root: pathlib.Path) -> int:
        """Return the synthetic handle for the worktree root."""
        assert root == self.root
        self.root_opens += 1
        return 1

    def open_relative_no_reparse(
        self, parent: int, component: str, *, for_read: bool
    ) -> int:
        """Record one component open, raising any modelled open failure first."""
        index = len(self.opened)
        assert component == self.parts[index], "open components in order"
        if self.open_error_index == index:
            assert self.open_error is not None
            raise self.open_error
        self.opened.append(component)
        handle = self.next_handle
        self.next_handle += 1
        return handle

    def is_reparse_point(self, handle: int) -> bool:
        """Report the modelled reparse flag for a synthetic handle."""
        return self._kind(self._prefix(handle))[2]

    def is_directory(self, handle: int) -> bool:
        """Report whether the modelled entry is a directory."""
        return self._kind(self._prefix(handle))[1] == "directory"

    def is_regular_file(self, handle: int) -> bool:
        """Report whether the modelled entry is a regular file."""
        return self._kind(self._prefix(handle))[1] == "file"

    def final_path(self, handle: int) -> pathlib.Path:
        """Return the modelled resolved path for a synthetic handle."""
        return self._kind(self._prefix(handle))[0]

    def descriptor_from_handle(self, handle: int) -> int:
        """Transfer the final file handle into a real descriptor for the test."""
        self.transferred = True
        self.transfers += 1
        return os.open(self.descriptor_file, os.O_RDONLY)

    def descriptor_path(self, file_descriptor: int) -> pathlib.Path:
        """Return the modelled path of a transferred descriptor."""
        return self.root.joinpath(*self.parts)

    def close_handle(self, handle: int) -> None:
        """Record that a synthetic handle was released."""
        self.closed.append(handle)


_FAILURE_EXPECTATION = {
    "reparse": (errno.ELOOP, r"reparse point"),
    "escape": (errno.EACCES, r"escaped the worktree"),
    "file": (errno.ENOTDIR, r"not a directory"),
    "directory": (errno.EISDIR, r"replaced by a directory"),
}


@st.composite
def _windows_walk_cases(draw: st.DrawFn) -> _WindowsCase:
    """Draw a tracked path that either succeeds or meets exactly one fault."""
    if draw(st.booleans()):
        return _WindowsCase(draw(SAFE_PARTS))
    # Reparse points and escapes fail anywhere; files and directories only where valid.
    parts = draw(SAFE_PARTS)
    final_index = len(parts) - 1
    kinds = ["reparse", "escape", "directory"]
    if final_index > 0:
        kinds.append("file")
    kind = draw(st.sampled_from(kinds))
    index = final_index
    if kind == "file":
        index = draw(st.integers(min_value=0, max_value=final_index - 1))
    elif kind != "directory":
        index = draw(st.integers(min_value=0, max_value=final_index))
    return _WindowsCase(parts, component=(index, kind))


@st.composite
def _windows_open_failure_cases(draw: st.DrawFn) -> _WindowsCase:
    """Draw a tracked path and one component open the modelled API refuses."""
    parts = draw(SAFE_PARTS)
    index = draw(st.integers(min_value=0, max_value=len(parts) - 1))
    error_number = draw(st.sampled_from([errno.EACCES, errno.EIO]))
    return _WindowsCase(parts, open_error=(index, error_number))


@st.composite
def _windows_unsafe_component_cases(draw: st.DrawFn) -> _WindowsCase:
    """Draw a tracked path with one component replaced by an unsafe name."""
    parts = draw(SAFE_PARTS)
    position = draw(st.integers(min_value=0, max_value=len(parts) - 1))
    parts[position] = draw(UNSAFE_COMPONENTS)
    return _WindowsCase(parts)


@settings(max_examples=60, deadline=None)
@given(_windows_walk_cases())
def test_windows_walk_opens_in_order_or_stops_at_the_offending_component(
    tmp_path_factory: pytest.TempPathFactory, case: _WindowsCase
) -> None:
    """A walk opens every component, or stops at the first refused one."""
    root = (tmp_path_factory.mktemp("windows-walk") / "repository").resolve()
    root.mkdir()
    descriptor_file = root / case.parts[-1]
    descriptor_file.write_text("Tracked text.\n", encoding="utf-8")
    api = _FakeWindowsApi(root, case, descriptor_file)

    with mock.patch.object(phrase_file_open_windows, "_windows_api", return_value=api):
        if case.component is None and case.open_error is None:
            file_descriptor = phrase_file_open_windows.open_windows_tracked_file(
                root, tuple(case.parts)
            )
            assert file_descriptor is not None
            with os.fdopen(file_descriptor, "rb") as stream:
                assert stream.read() == b"Tracked text.\n"
            assert api.opened == case.parts, "open every component in order"
            assert api.transfers == 1, "transfer the final handle exactly once"
            assert sorted(api.closed) == api.expected_closed
            return
        assert case.component is not None
        index, kind = case.component
        expected_errno, message = _FAILURE_EXPECTATION[kind]

        with pytest.raises(OSError, match=message) as error:
            phrase_file_open_windows.open_windows_tracked_file(root, tuple(case.parts))

        assert error.value.errno == expected_errno
        assert api.opened == case.parts[: index + 1], "stop at the offending component"
        assert not api.transferred, "never transfer a refused handle"
        assert sorted(api.closed) == api.expected_closed


@settings(max_examples=50, deadline=None)
@given(_windows_open_failure_cases())
def test_windows_walk_propagates_an_injected_open_failure(
    tmp_path_factory: pytest.TempPathFactory, case: _WindowsCase
) -> None:
    """A failing component open stops the walk and releases earlier handles."""
    root = (tmp_path_factory.mktemp("windows-open-failure") / "repository").resolve()
    root.mkdir()
    api = _FakeWindowsApi(root, case, root / case.parts[-1])
    assert case.open_error is not None
    index, error_number = case.open_error

    with (
        mock.patch.object(phrase_file_open_windows, "_windows_api", return_value=api),
        pytest.raises(OSError, match="injected open failure") as error,
    ):
        phrase_file_open_windows.open_windows_tracked_file(root, tuple(case.parts))

    assert error.value is api.open_error, "propagate the exact injected failure"
    assert error.value.errno == error_number
    assert api.opened == case.parts[:index], "open no component after the failure"
    assert not api.transferred
    assert sorted(api.closed) == api.expected_closed, "release the root with the rest"


@settings(max_examples=60, deadline=None)
@given(_windows_unsafe_component_cases())
def test_windows_walk_rejects_unsafe_components_before_any_open(
    case: _WindowsCase,
) -> None:
    """An unsafe component is refused before the Windows API is consulted."""
    root = pathlib.Path("repository")
    api = _FakeWindowsApi(root, case, root / "unused.md")
    api_factory = mock.Mock(return_value=api)

    with (
        mock.patch.object(phrase_file_open_windows, "_windows_api", api_factory),
        pytest.raises(OSError, match="safe components") as error,
    ):
        phrase_file_open_windows.open_windows_tracked_file(root, tuple(case.parts))

    assert error.value.errno == errno.EINVAL
    api_factory.assert_not_called()
    assert api.root_opens == 0, "the root is never opened"
    assert not api.opened, "no relative component is opened"
    assert not api.transferred
