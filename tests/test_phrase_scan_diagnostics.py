"""Verify path-free logs for phrase-scan eligibility and read decisions."""

import logging
import os
import pathlib
import typing as typ

import pytest

from typos_config_builder import phrases_files


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not pathlib.Path("/dev/zero").exists(),
    reason="requires FIFO and device support",
)
def test_ineligible_inputs_log_fixed_path_free_reasons(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Fixed reason records expose skips without leaking tracked names."""
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "guide.md").write_text("outside", encoding="utf-8")

    symlink = repository / "link.md"
    symlink.symlink_to("/dev/zero")
    (repository / "docs").symlink_to(outside, target_is_directory=True)
    fifo = repository / "pipe.md"
    os.mkfifo(fifo)
    oversized = repository / "large.md"
    oversized.write_bytes(b"x" * 9)
    cases = (
        (symlink, repository, "skipped-symlink"),
        (repository / "docs" / "guide.md", repository, "skipped-outside-worktree"),
        (fifo, repository, "skipped-non-regular"),
        (pathlib.Path("/dev/zero"), pathlib.Path("/dev"), "skipped-non-regular"),
        (oversized, repository, "skipped-oversized"),
    )

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder.phrases_files"):
        for candidate, root, decision in cases:
            assert not phrases_files.is_scannable(
                candidate, root.resolve(), max_file_size_bytes=8
            ), f"{decision} inputs must be rejected"

    records = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    ]
    assert [getattr(record, "decision", None) for record in records] == [
        decision for _, _, decision in cases
    ], "each rejected input must have a fixed reason"
    assert all(record.getMessage() == "Phrase scan decision" for record in records)
    assert all(not hasattr(record, "path") for record in records)
    assert all(not hasattr(record, "content") for record in records)


def test_replaced_directory_logs_fail_closed_reason(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Directory replacement stays an error with a bounded diagnostic."""
    directory = tmp_path / "README.md"
    directory.mkdir()

    with (
        caplog.at_level(logging.DEBUG, logger="typos_config_builder.phrases_files"),
        pytest.raises(IsADirectoryError),
    ):
        phrases_files.is_scannable(directory, tmp_path.resolve(), max_file_size_bytes=8)

    record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    )
    assert getattr(record, "decision", None) == "failed-directory-replacement"
    assert getattr(record, "error_class", None) == "directory"
    assert record.getMessage() == "Phrase scan decision"


def test_read_error_logs_bounded_reason(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read failures stay fail-closed and omit paths from diagnostics."""

    def fail_open(*_args: object, **_kwargs: object) -> typ.Never:
        """Simulate a file that cannot be opened."""
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "open", fail_open)
    path = tmp_path / "README.md"

    with (
        caplog.at_level(logging.DEBUG, logger="typos_config_builder.phrases_files"),
        pytest.raises(phrases_files.PhraseScanError),
    ):
        phrases_files.read_tracked_text(path, pathlib.Path("README.md"))

    record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    )
    assert getattr(record, "decision", None) == "failed-read"
    assert getattr(record, "error_class", None) == "file-system"
    assert record.getMessage() == "Phrase scan decision"
