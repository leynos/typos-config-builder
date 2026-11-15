"""Property contracts for the phrase-input path walkers.

Component validation and both platform walkers are the security boundary for
phrase inputs. Rather than exemplifying them, these tests state the invariants
over generated component lists and generated walking depths: a walk opens the
tracked components in order and nothing else, a failure stops at the first
offending component, and every handle the walk acquired is released.
"""

from __future__ import annotations

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
# ``pathlib`` normalises a ``"."`` component away, so a parent component is
# the only unsafe name a tracked path can still carry.
UNSAFE_COMPONENT = st.just("..")

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
    relative = pathlib.Path("/").joinpath(*parts)

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


class _FakeWindowsApi:
    """Model handle-relative traversal with one injected component failure.

    The model owns no host handles: it records which synthetic handle each
    component produced so a test can prove the walk stopped at the right
    component and released everything it had acquired.
    """

    def __init__(
        self,
        root: pathlib.Path,
        parts: list[str],
        failure: tuple[int, str] | None,
        descriptor_file: pathlib.Path,
    ) -> None:
        """Record the generated path and the failure to inject, if any."""
        self.root = root
        self.parts = parts
        self.failure = failure
        self.descriptor_file = descriptor_file
        self.opened: list[str] = []
        self.closed: list[int] = []
        self.next_handle = 2
        self.transferred = False

    def _kind(self, prefix: tuple[str, ...]) -> tuple[pathlib.Path, str, bool]:
        """Return the modelled path, entry type, and reparse flag for a prefix."""
        index = len(prefix) - 1
        is_final = index == len(self.parts) - 1
        path = self.root.joinpath(*prefix)
        if self.failure is not None and self.failure[0] == index:
            if self.failure[1] == "reparse":
                return path, "directory", True
            if self.failure[1] == "escape":
                return self.root.parent / "outside" / path.name, "directory", False
            return path, "file", False
        return path, "file" if is_final else "directory", False

    def _prefix(self, handle: int) -> tuple[str, ...]:
        """Return the modelled path prefix a synthetic handle names."""
        return () if handle == 1 else tuple(self.parts[: handle - 1])

    def open_root(self, root: pathlib.Path) -> int:
        """Return the synthetic handle for the worktree root."""
        assert root == self.root
        return 1

    def open_relative_no_reparse(
        self, parent: int, component: str, *, for_read: bool
    ) -> int:
        """Record one component open and produce its synthetic handle."""
        assert component == self.parts[len(self.opened)], "open components in order"
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
        return os.open(self.descriptor_file, os.O_RDONLY)

    def descriptor_path(self, file_descriptor: int) -> pathlib.Path:
        """Return the modelled path of a transferred descriptor."""
        return self.root.joinpath(*self.parts)

    def close_handle(self, handle: int) -> None:
        """Record that a synthetic handle was released."""
        self.closed.append(handle)


WINDOWS_FAILURE = st.one_of(
    st.none(),
    st.tuples(
        st.integers(min_value=0, max_value=2),
        st.sampled_from(["reparse", "escape", "file"]),
    ),
)


@settings(max_examples=60, deadline=None)
@given(SAFE_PARTS, WINDOWS_FAILURE)
def test_windows_walk_stops_at_the_offending_component(
    tmp_path_factory: pytest.TempPathFactory,
    parts: list[str],
    failure: tuple[int, str] | None,
) -> None:
    """A refused component ends the walk and releases every acquired handle."""
    root = (tmp_path_factory.mktemp("windows-walk") / "repository").resolve()
    root.mkdir()
    descriptor_file = root / parts[-1]
    descriptor_file.write_text("Tracked text.\n", encoding="utf-8")
    # Only an intermediate component can fail before the final file is reached.
    injected = failure if failure is not None and failure[0] < len(parts) - 1 else None
    api = _FakeWindowsApi(root, parts, injected, descriptor_file)

    def api_factory() -> _FakeWindowsApi:
        """Return the modelled API for this one walk."""
        return api

    with mock.patch.object(phrase_file_open_windows, "_windows_api", api_factory):
        if injected is None:
            file_descriptor = phrase_file_open_windows.open_windows_tracked_file(
                root, tuple(parts)
            )
            assert file_descriptor is not None, "an ordinary file must open"
            os.close(file_descriptor)
            assert api.opened == parts, "open every tracked component in order"
        else:
            with pytest.raises(
                OSError, match=r"reparse point|escaped the worktree|not a directory"
            ):
                phrase_file_open_windows.open_windows_tracked_file(root, tuple(parts))
            assert len(api.opened) == injected[0] + 1, "stop at the offending component"
            assert not api.transferred, "never transfer a refused handle"

    transferred_handle = api.next_handle - 1 if api.transferred else None
    expected_closed = [
        handle for handle in range(1, api.next_handle) if handle != transferred_handle
    ]
    assert sorted(api.closed) == expected_closed, (
        "release every handle the walk acquired and did not transfer"
    )
