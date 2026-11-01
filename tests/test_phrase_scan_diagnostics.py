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

from typos_config_builder import phrases, phrases_files


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
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        assert not phrases_files.is_scannable(link, root, max_file_size_bytes=8)
        assert not phrases_files.is_scannable(fifo, root, max_file_size_bytes=8)
        with pytest.raises(IsADirectoryError):
            phrases_files.is_scannable(directory, root, max_file_size_bytes=8)
        assert not phrases_files.is_scannable(oversized, root, max_file_size_bytes=8)
        assert phrases_files.scan_eligibility(link, root).decision == "skipped-symlink"
        assert (
            phrases_files.scan_eligibility(fifo, root).decision == "skipped-non-regular"
        )
        assert (
            phrases_files.scan_eligibility(
                oversized, root, max_file_size_bytes=8
            ).decision
            == "skipped-oversized"
        )

        monkeypatch.setattr(
            pathlib.Path,
            "open",
            lambda *_args, **_kwargs: io.BytesIO(b"x" * 9),
        )
        too_large = phrases_files.read_tracked_text(link, pathlib.Path("device.md"))
        assert too_large.text is None
        assert too_large.outcome.decision == "skipped-oversized"

        monkeypatch.setattr(
            pathlib.Path,
            "open",
            lambda *_args, **_kwargs: io.BytesIO(b"\xff"),
        )
        binary = phrases_files.read_tracked_text(link, pathlib.Path("device.md"))
        assert binary.text is None
        assert binary.outcome.decision == "skipped-binary"

        def fail_open(*_args: object, **_kwargs: object) -> typ.Never:
            """Simulate a tracked input that cannot be opened."""
            raise PermissionError

        monkeypatch.setattr(pathlib.Path, "open", fail_open)
        with pytest.raises(phrases_files.PhraseScanError):
            phrases_files.read_tracked_text(link, pathlib.Path("device.md"))

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

    records = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    ]
    assert not findings
    assert [
        (getattr(record, "decision", None), getattr(record, "error_class", None))
        for record in records
    ] == [
        ("skipped-symlink", "symlink"),
        ("skipped-binary", "not-utf-8"),
        ("skipped-outside-worktree", "containment"),
        ("skipped-oversized", "size-limit"),
        ("skipped-non-regular", "non-regular"),
    ]
    assert all(record.getMessage() == "Phrase scan decision" for record in records)
    assert all(not hasattr(record, "path") for record in records)
    assert all(not hasattr(record, "content") for record in records)


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

    record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    )
    assert getattr(record, "decision", None) == "failed-directory-replacement"
    assert getattr(record, "error_class", None) == "directory"
    assert record.getMessage() == "Phrase scan decision"
    assert not hasattr(record, "path")
    assert not hasattr(record, "content")


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

    def fail_open(*_args: object, **_kwargs: object) -> typ.Never:
        """Simulate a tracked file that cannot be opened."""
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "open", fail_open)
    with (
        caplog.at_level(logging.DEBUG, logger="typos_config_builder"),
        pytest.raises(phrases.PhraseScanError),
    ):
        phrases.find_phrases(repository, policy)

    record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    )
    assert getattr(record, "decision", None) == "failed-read"
    assert getattr(record, "error_class", None) == "file-system"
    assert record.getMessage() == "Phrase scan decision"
    assert not hasattr(record, "path")
    assert not hasattr(record, "content")


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
    source_path = repository / "README.md"
    original_open = pathlib.Path.open

    def growing_open(path: pathlib.Path, _mode: str) -> typ.BinaryIO:
        """Present an over-limit stream after metadata was accepted."""
        if path == source_path:
            return io.BytesIO(b"x" * 9)
        return original_open(path, "rb")

    monkeypatch.setattr(pathlib.Path, "open", growing_open)
    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        findings = phrases.find_phrases(repository, policy)

    records = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    ]
    assert not findings
    assert len(records) == 1
    assert getattr(records[0], "decision", None) == "skipped-oversized"
    assert getattr(records[0], "error_class", None) == "size-limit"
    assert records[0].getMessage() == "Phrase scan decision"
    assert not hasattr(records[0], "path")
    assert not hasattr(records[0], "content")


@given(
    limit=st.integers(min_value=0, max_value=512),
    payload_size=st.integers(min_value=0, max_value=1024),
)
@example(limit=0, payload_size=0)
@example(limit=0, payload_size=1)
@example(limit=8, payload_size=8)
@example(limit=8, payload_size=9)
def test_bounded_read_obeys_nonnegative_limits(
    limit: int,
    payload_size: int,
) -> None:
    """A bounded read requests limit plus one and accepts only in-bound text."""
    requested_sizes: list[int | None] = []

    class ReadProbe(io.BytesIO):
        """Record the byte bound requested from the reader."""

        def read(self, size: int | None = -1) -> bytes:
            """Capture the bound while returning the generated payload."""
            requested_sizes.append(size)
            return super().read(size)

    def open_probe(_path: pathlib.Path, _mode: str) -> typ.BinaryIO:
        """Supply a probe stream to record the bounded read request."""
        return ReadProbe(b"x" * payload_size)

    with (
        mock.patch.object(phrases_files, "MAX_FILE_SIZE_BYTES", limit),
        mock.patch.object(pathlib.Path, "open", open_probe),
    ):
        result = phrases_files.read_tracked_text(
            pathlib.Path("README.md"), pathlib.Path("README.md")
        )

    is_oversized = payload_size > limit
    assert requested_sizes == [limit + 1]
    assert (result.text is None) is is_oversized
    assert result.outcome.decision == (
        "skipped-oversized" if is_oversized else "scanned-text"
    )
    if result.text is not None:
        assert result.text == "x" * payload_size
