"""Verify command-layer publication of phrase-scan diagnostics and metrics.

The query returns fixed outcomes and publishes nothing, so this module owns
every log record and metric line a phrase scan produces. The labels are fixed
categories: no tracked path, content, or raw error text may reach either
channel.
"""

from __future__ import annotations

import io
import logging
import pathlib
import typing as typ

from typos_config_builder import phrase_scan_reporting, phrases
from typos_config_builder.phrase_scan_reporting import metric_counts

if typ.TYPE_CHECKING:
    import pytest

METRIC_PREFIX = "metric "


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


def _metric_lines(captured: pytest.CaptureFixture[str]) -> list[str]:
    """Return the metric lines written to standard error."""
    return [
        line
        for line in captured.readouterr().err.splitlines()
        if line.startswith(METRIC_PREFIX)
    ]


def test_outcomes_are_logged_once_each_without_tracked_input(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every published outcome yields one path-free record and one count."""
    outcomes = (
        phrases.SkipReason.SYMLINK,
        phrases.SkipReason.BINARY,
        phrases.SkipReason.OUTSIDE_WORKTREE,
        phrases.SkipReason.OVERSIZED,
        phrases.SkipReason.NON_REGULAR,
    )

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        phrase_scan_reporting.publish_scan_outcomes(outcomes)

    records = _phrase_scan_records(caplog)
    assert [
        (getattr(record, "decision", None), getattr(record, "error_class", None))
        for record in records
    ] == [
        ("skipped-symlink", "symlink"),
        ("skipped-binary", "not-utf-8"),
        ("skipped-outside-worktree", "containment"),
        ("skipped-oversized", "size-limit"),
        ("skipped-non-regular", "non-regular"),
    ], "log each fixed outcome once"
    for record in records:
        _assert_path_free(record)
    assert _metric_lines(capsys) == [
        "metric phrase_scan_files_skipped_total{reason=binary}=1",
        "metric phrase_scan_files_skipped_total{reason=non-regular}=1",
        "metric phrase_scan_files_skipped_total{reason=outside-worktree}=1",
        "metric phrase_scan_files_skipped_total{reason=oversized}=1",
        "metric phrase_scan_files_skipped_total{reason=symlink}=1",
    ], "emit one sorted counter line per fixed reason"


def test_repeated_reasons_are_counted_together(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Repeated outcomes aggregate into a single bounded counter line."""
    outcomes = (
        phrases.SkipReason.BINARY,
        phrases.SkipReason.BINARY,
        phrases.SkipReason.SYMLINK,
    )

    phrase_scan_reporting.publish_scan_outcomes(outcomes)

    assert _metric_lines(capsys) == [
        "metric phrase_scan_files_skipped_total{reason=binary}=2",
        "metric phrase_scan_files_skipped_total{reason=symlink}=1",
    ], "count each fixed reason once"


def test_a_supplied_writer_captures_metrics_without_standard_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller can collect the metric lines in a writer of its own."""
    stream = io.StringIO()

    phrase_scan_reporting.publish_scan_outcomes(
        (phrases.SkipReason.OVERSIZED,), stream=stream
    )
    phrase_scan_reporting.publish_scan_failure(
        phrases.PhraseScanError(
            pathlib.Path("README.md"), phrases.ScanFailureReason.READ
        ),
        stream=stream,
    )

    assert stream.getvalue().splitlines() == [
        "metric phrase_scan_files_skipped_total{reason=oversized}=1",
        "metric phrase_scan_files_failed_total{reason=read-failure}=1",
    ], "write both counters to the supplied writer"
    assert not _metric_lines(capsys), (
        "the supplied writer replaces the process's standard error"
    )


def test_a_clean_scan_publishes_nothing(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scan with no skips writes no diagnostic and no metric."""
    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        phrase_scan_reporting.publish_scan_outcomes(())

    assert not _phrase_scan_records(caplog), "publish no record for a clean scan"
    assert not _metric_lines(capsys), "publish no metric for a clean scan"


def test_failure_is_published_as_a_failure_counter(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fail-closed scan is reported under the failure counter."""
    error = phrases.PhraseScanError(
        pathlib.Path("README.md"), phrases.ScanFailureReason.READ
    )

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        phrase_scan_reporting.publish_scan_failure(error)

    record = next(iter(_phrase_scan_records(caplog)))
    assert getattr(record, "decision", None) == "failed-read", "log the fixed decision"
    _assert_path_free(record)
    captured = capsys.readouterr()
    assert [
        line for line in captured.err.splitlines() if line.startswith(METRIC_PREFIX)
    ] == ["metric phrase_scan_files_failed_total{reason=read-failure}=1"], (
        "count the failure under its fixed reason"
    )
    assert "README" not in captured.err, "never publish a tracked path"
    assert "README" not in captured.out, "never print a tracked path"


def test_failure_counter_is_separate_from_the_skip_counter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure and skip counters never share a label set."""
    phrase_scan_reporting.publish_scan_failure(
        phrases.PhraseScanError(
            pathlib.Path("README.md"), phrases.ScanFailureReason.METADATA
        )
    )

    assert metric_counts((phrases.SkipReason.OVERSIZED,)) == {"oversized": 1}
    assert not metric_counts(()), "a successful read is not a skip"
    assert _metric_lines(capsys) == [
        "metric phrase_scan_files_failed_total{reason=metadata-failure}=1"
    ], "count the failure under its own fixed label"
