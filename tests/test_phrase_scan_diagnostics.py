"""Verify typed phrase-scan decisions and path-free orchestration logs."""

import io
import logging
import os
import pathlib
import shutil
import typing as typ
from unittest import mock

import pytest
from conftest import build_repository, cache_text
from hypothesis import example, given
from hypothesis import strategies as st

from typos_config_builder import phrase_scan, phrases, phrases_files


def _phrase_scan_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return only records emitted at the phrase-scan boundary."""
    return [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    ]


def _assert_path_free(record: logging.LogRecord) -> None:
    """Check that a diagnostic contains fixed text and no tracked input."""
    assert record.getMessage() == "Phrase scan decision", "keep diagnostic text fixed"
    assert not hasattr(record, "path"), "do not expose tracked paths"
    assert not hasattr(record, "content"), "do not expose tracked contents"


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not pathlib.Path("/dev/zero").exists(),
    reason="requires FIFO and device support",
)
def test_file_layer_decisions_are_silent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File helpers return fixed outcomes without owning diagnostics."""
    root = tmp_path.resolve()
    link = root / "device.md"
    link.symlink_to("/dev/zero")
    fifo = root / "pipe.md"
    os.mkfifo(fifo)
    directory = root / "directory.md"
    directory.mkdir()
    oversized = root / "large.md"
    oversized.write_bytes(b"x" * 9)
    (root / "README.md").write_bytes(b"small")
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        assert not phrases_files.is_scannable(link, root, max_file_size_bytes=8), (
            "skip symbolic links"
        )
        assert not phrases_files.is_scannable(fifo, root, max_file_size_bytes=8), (
            "skip FIFOs"
        )
        with pytest.raises(IsADirectoryError):
            phrases_files.is_scannable(directory, root, max_file_size_bytes=8)
        assert not phrases_files.is_scannable(oversized, root, max_file_size_bytes=8), (
            "skip oversized files"
        )
        assert (
            phrases_files.scan_eligibility(link, root).decision == "skipped-symlink"
        ), "classify symbolic links"
        assert (
            phrases_files.scan_eligibility(fifo, root).decision == "skipped-non-regular"
        ), "classify non-regular entries"
        assert (
            phrases_files.scan_eligibility(
                oversized, root, max_file_size_bytes=8
            ).decision
            == "skipped-oversized"
        ), "classify oversized files"

        reader = io.BytesIO(b"x" * 9)
        monkeypatch.setattr(os, "read", lambda _fd, size: reader.read(size))
        too_large = phrases_files.read_tracked_text(root, pathlib.Path("README.md"))
        assert too_large.text is None, "discard over-limit bytes"
        assert too_large.outcome.decision == "skipped-oversized", "record size skip"

        reader = io.BytesIO(b"\xff")
        monkeypatch.setattr(os, "read", lambda _fd, size: reader.read(size))
        binary = phrases_files.read_tracked_text(root, pathlib.Path("README.md"))
        assert binary.text is None, "discard binary content"
        assert binary.outcome.decision == "skipped-binary", "record binary skip"

        def fail_read(*_args: object, **_kwargs: object) -> typ.Never:
            """Simulate a tracked input that cannot be read."""
            raise PermissionError

        monkeypatch.setattr(os, "read", fail_read)
        with pytest.raises(phrases_files.PhraseScanError):
            phrases_files.read_tracked_text(root, pathlib.Path("README.md"))

    assert not caplog.records, "file-layer helpers must not emit diagnostics"


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not pathlib.Path("/dev/zero").exists(),
    reason="requires FIFO and device support",
)
def test_orchestration_logs_fixed_path_free_eligibility_decisions(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The phrase boundary logs fixed categories without tracked names."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": "Ordinary text.\n",
            "binary.md": "Initially UTF-8.\n",
            "docs/guide.md": "Ordinary text.\n",
            "large.md": "Initially small.\n",
            "pipe.md": "Ordinary text.\n",
        },
    )
    (repository / "binary.md").write_bytes(b"\xff\xfe\x00binary")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "guide.md").write_text("Outside text.\n", encoding="utf-8")
    shutil.rmtree(repository / "docs")
    (repository / "docs").symlink_to(outside, target_is_directory=True)
    (repository / "README.md").unlink()
    (repository / "README.md").symlink_to("/dev/zero")
    (repository / "pipe.md").unlink()
    os.mkfifo(repository / "pipe.md")
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 12)

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    records = _phrase_scan_records(caplog)
    assert not findings, "skip ineligible or binary tracked inputs"
    assert [
        (getattr(record, "decision", None), getattr(record, "error_class", None))
        for record in records
    ] == [
        ("skipped-symlink", "symlink"),
        ("skipped-binary", "not-utf-8"),
        ("skipped-outside-worktree", "containment"),
        ("skipped-oversized", "size-limit"),
        ("skipped-non-regular", "non-regular"),
    ], "log each fixed eligibility and read outcome"
    for record in records:
        _assert_path_free(record)


def test_directory_replacement_logs_without_path_or_content(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Directory replacement fails closed with a fixed path-free diagnostic."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    (repository / "README.md").unlink()
    (repository / "README.md").mkdir()
    policy = phrases.load_policy(repository)

    with (
        caplog.at_level(logging.DEBUG, logger="typos_config_builder"),
        pytest.raises(phrases.PhraseScanError),
    ):
        phrases.find_phrases(repository, policy)

    record = next(iter(_phrase_scan_records(caplog)))
    assert getattr(record, "decision", None) == "failed-directory-replacement", (
        "classify a directory replacement as a scan failure"
    )
    assert getattr(record, "error_class", None) == "directory", (
        "use the fixed directory error label"
    )
    _assert_path_free(record)


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
    reason="requires no-follow directory-relative opening",
)
def test_parent_symlink_swap_cannot_redirect_the_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent replaced after eligibility cannot redirect the open outside."""
    repository = tmp_path / "repository"
    nested = repository / "nested"
    nested.mkdir(parents=True)
    candidate = nested / "README.md"
    candidate.write_text("Safe tracked text.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "README.md").write_text("External text.\n", encoding="utf-8")
    root = repository.resolve()
    relative = pathlib.Path("nested/README.md")
    original_open = phrases_files.open_tracked_file
    original_read = os.read
    read_calls: list[int] = []

    def swap_parent_then_open(
        worktree: pathlib.Path, tracked_path: pathlib.Path
    ) -> int | None:
        """Replace the parent only after the path passed its eligibility check."""
        nested.rename(tmp_path / "original-nested")
        nested.symlink_to(outside, target_is_directory=True)
        return original_open(worktree, tracked_path)

    def observe_read(file_descriptor: int, size: int) -> bytes:
        """Record any content read after the unsafe path replacement."""
        read_calls.append(size)
        return original_read(file_descriptor, size)

    monkeypatch.setattr(phrases_files, "open_tracked_file", swap_parent_then_open)
    monkeypatch.setattr(os, "read", observe_read)

    with pytest.raises(phrases.PhraseScanError):
        phrase_scan.read_scannable_text(root / relative, relative, root)

    assert not read_calls, "reject the swapped parent before reading file content"


def test_read_error_logs_without_path_or_content(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read failures remain fail-closed without exposing tracked input."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    policy = phrases.load_policy(repository)
    tracked_file_descriptors: set[int] = set()
    original_open = phrases_files.open_tracked_file
    original_read = os.read

    def record_open_file(worktree: pathlib.Path, relative: pathlib.Path) -> int | None:
        """Remember descriptors so subprocess pipes keep their real reader."""
        file_descriptor = original_open(worktree, relative)
        if file_descriptor is not None:
            tracked_file_descriptors.add(file_descriptor)
        return file_descriptor

    def fail_read(file_descriptor: int, size: int) -> bytes:
        """Simulate a tracked file that cannot be read."""
        if file_descriptor in tracked_file_descriptors:
            raise PermissionError
        return original_read(file_descriptor, size)

    monkeypatch.setattr(phrases_files, "open_tracked_file", record_open_file)
    monkeypatch.setattr(os, "read", fail_read)
    with (
        caplog.at_level(logging.DEBUG, logger="typos_config_builder"),
        pytest.raises(phrases.PhraseScanError),
    ):
        phrases.find_phrases(repository, policy)

    record = next(iter(_phrase_scan_records(caplog)))
    assert getattr(record, "decision", None) == "failed-read", (
        "classify an unreadable tracked file as a scan failure"
    )
    assert getattr(record, "error_class", None) == "file-system", (
        "use the fixed filesystem error label"
    )
    _assert_path_free(record)


def test_read_side_oversize_logs_without_path_or_content(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that grows after its metadata check is skipped and logged safely."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "small\n"},
    )
    policy = phrases.load_policy(repository)
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)
    tracked_file_descriptors: set[int] = set()
    original_open = phrases_files.open_tracked_file
    original_read = os.read
    reader = io.BytesIO(b"x" * 9)

    def record_open_file(worktree: pathlib.Path, relative: pathlib.Path) -> int | None:
        """Remember the input descriptor without changing subprocess reads."""
        file_descriptor = original_open(worktree, relative)
        if file_descriptor is not None:
            tracked_file_descriptors.add(file_descriptor)
        return file_descriptor

    def growing_read(file_descriptor: int, size: int) -> bytes:
        """Present an over-limit stream after metadata was accepted."""
        if file_descriptor in tracked_file_descriptors:
            return reader.read(size)
        return original_read(file_descriptor, size)

    monkeypatch.setattr(phrases_files, "open_tracked_file", record_open_file)
    monkeypatch.setattr(os, "read", growing_read)
    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        findings = phrases.find_phrases(repository, policy)

    records = _phrase_scan_records(caplog)
    assert not findings, "skip an input that grows beyond its checked size"
    assert len(records) == 1, "log one outcome for the oversized input"
    assert getattr(records[0], "decision", None) == "skipped-oversized", (
        "classify the growth race as oversized"
    )
    assert getattr(records[0], "error_class", None) == "size-limit", (
        "use the fixed size-limit error label"
    )
    _assert_path_free(records[0])


@given(
    limit=st.integers(min_value=0, max_value=512),
    payload_size=st.integers(min_value=0, max_value=1024),
)
@example(limit=0, payload_size=0)
@example(limit=0, payload_size=1)
@example(limit=8, payload_size=8)
@example(limit=8, payload_size=9)
def test_bounded_read_obeys_nonnegative_limits(
    tmp_path_factory: pytest.TempPathFactory,
    limit: int,
    payload_size: int,
) -> None:
    """A bounded read consumes at most one byte beyond the configured limit."""
    requested_sizes: list[int] = []
    returned_sizes: list[int] = []
    repository = tmp_path_factory.mktemp("bounded-read")

    (repository / "README.md").write_bytes(b"")
    reader = io.BytesIO(b"x" * payload_size)

    def read_probe(_fd: int, size: int) -> bytes:
        """Supply generated bytes and record each bounded read request."""
        requested_sizes.append(size)
        chunk = reader.read(size)
        returned_sizes.append(len(chunk))
        return chunk

    with (
        mock.patch.object(phrases_files, "MAX_FILE_SIZE_BYTES", limit),
        mock.patch.object(os, "read", read_probe),
    ):
        result = phrases_files.read_tracked_text(repository, pathlib.Path("README.md"))

    is_oversized = payload_size > limit
    assert requested_sizes[0] == limit + 1, "start with the permitted read size"
    assert all(size <= limit + 1 for size in requested_sizes), "cap each read request"
    assert sum(returned_sizes) == min(payload_size, limit + 1), (
        "consume no more than one byte past the limit"
    )
    assert (result.text is None) is is_oversized, "accept only in-bound payloads"
    assert result.outcome.decision == (
        "skipped-oversized" if is_oversized else "scanned-text"
    ), "report the expected bounded-read outcome"
    if result.text is not None:
        assert result.text == "x" * payload_size, "preserve accepted UTF-8 text"
