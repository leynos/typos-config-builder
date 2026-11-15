"""Publish path-free phrase-scan diagnostics and bounded outcome metrics.

The phrase query returns domain skip reasons and never logs. Command-level
code calls this module to publish them, so a library caller can run the scan
without inheriting an observability side effect.

This module also owns the one mapping from a domain reason to its fixed log
and metric labels. Typed outcomes carry categories, not presentation: keeping
the labels here means a new reason cannot silently reach a log record without
an explicit mapping.

Metrics use those fixed labels; tracked paths, contents, and raw error text
never become labels. They are written as ``metric name{reason=...}=count``
lines on standard error, the machine-readable channel Continuous Integration
(CI) logs already collect.
"""

from __future__ import annotations

import logging
import sys
import types
import typing as typ

from typos_config_builder import phrase_source, phrases

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

LOGGER = logging.getLogger(__name__)

#: Skipped inputs, counted per fixed reason label.
SKIPPED_METRIC = "phrase_scan_files_skipped_total"

#: Fail-closed inputs, counted per fixed reason label.
FAILED_METRIC = "phrase_scan_files_failed_total"

#: Fixed ``(decision, error_class, metric_reason)`` labels for each skip.
_SKIP_LABELS: cabc.Mapping[phrases.SkipReason, tuple[str, str, str]] = (
    types.MappingProxyType({
        phrases.SkipReason.SYMLINK: ("skipped-symlink", "symlink", "symlink"),
        phrases.SkipReason.OUTSIDE_WORKTREE: (
            "skipped-outside-worktree",
            "containment",
            "outside-worktree",
        ),
        phrases.SkipReason.NON_REGULAR: (
            "skipped-non-regular",
            "non-regular",
            "non-regular",
        ),
        phrases.SkipReason.OVERSIZED: ("skipped-oversized", "size-limit", "oversized"),
        phrases.SkipReason.BINARY: ("skipped-binary", "not-utf-8", "binary"),
    })
)

#: Fixed ``(decision, error_class, metric_reason)`` labels for each failure.
_FAILURE_LABELS: cabc.Mapping[phrases.ScanFailureReason, tuple[str, str, str]] = (
    types.MappingProxyType({
        phrases.ScanFailureReason.METADATA: (
            "failed-metadata",
            "file-system",
            "metadata-failure",
        ),
        phrases.ScanFailureReason.READ: ("failed-read", "file-system", "read-failure"),
        phrases.ScanFailureReason.DIRECTORY_REPLACEMENT: (
            "failed-directory-replacement",
            "directory",
            "directory-replacement",
        ),
    })
)


def _log_label(decision: str, error_class: str) -> None:
    """Log one fixed decision without exposing a tracked path or content."""
    LOGGER.debug(
        "Phrase scan decision",
        extra={
            "operation": "phrase-scan",
            "decision": decision,
            "error_class": error_class,
        },
    )


def _emit_metric(name: str, reason: str, count: int) -> None:
    """Write one bounded metric line for a log-based collector."""
    sys.stderr.write(f"metric {name}{{reason={reason}}}={count}\n")


def _emit_counts(name: str, counts: cabc.Mapping[str, int]) -> None:
    """Write one metric line per fixed reason that occurred."""
    for reason, count in sorted(counts.items()):
        _emit_metric(name, reason, count)


def metric_counts(outcomes: cabc.Iterable[phrases.SkipReason]) -> dict[str, int]:
    """Count skip reasons by their fixed metric label.

    Parameters
    ----------
    outcomes
        Domain skip reasons collected by a phrase scan.

    Returns
    -------
    dict[str, int]
        Count per fixed metric label, in first-seen order. Labels are stable
        and carry no path, content, or raw error text.

    Examples
    --------
    >>> metric_counts((phrases.SkipReason.BINARY, phrases.SkipReason.BINARY))
    {'binary': 2}
    """
    counts: dict[str, int] = {}
    for reason in outcomes:
        label = _SKIP_LABELS[reason][2]
        counts[label] = counts.get(label, 0) + 1
    return counts


def publish_scan_outcomes(outcomes: cabc.Iterable[phrases.SkipReason]) -> None:
    """Publish fixed diagnostics and skip metrics for a completed scan.

    Parameters
    ----------
    outcomes
        Domain skip reasons returned by the phrase query.

    Examples
    --------
    >>> publish_scan_outcomes(())  # no skips means no metric line
    """
    collected = tuple(outcomes)
    for reason in collected:
        decision, error_class, _ = _SKIP_LABELS[reason]
        _log_label(decision, error_class)
    _emit_counts(SKIPPED_METRIC, metric_counts(collected))


def publish_scan_failure(error: phrases.PhraseScanError) -> None:
    """Publish the fixed diagnostic and failure metric for an aborted scan.

    Parameters
    ----------
    error
        The fail-closed error raised by the phrase query.

    Examples
    --------
    >>> publish_scan_failure(  # doctest: +SKIP
    ...     phrases.PhraseScanError(
    ...         pathlib.Path("README.md"), phrases.ScanFailureReason.READ
    ...     )
    ... )
    """
    decision, error_class, metric_reason = _FAILURE_LABELS[error.reason]
    _log_label(decision, error_class)
    _emit_metric(FAILED_METRIC, metric_reason, 1)


def find_and_publish(
    repository: pathlib.Path, policy: phrases.PhrasePolicy
) -> phrases.PhraseScanResult:
    """Run the phrase query over a worktree and publish whatever it returns.

    The query never publishes, so every command that runs it composes the two
    here: one completed scan reports its skip metrics, and one failed scan
    reports its failure metric before the error reaches the caller. A command
    cannot publish a success and forget the failure, or the reverse.

    Parameters
    ----------
    repository
        Git worktree to scan.
    policy
        Merged phrase policy applied to tracked text.

    Returns
    -------
    phrases.PhraseScanResult
        Findings and fixed outcomes returned by the query.

    Raises
    ------
    phrases.PhraseScanError
        If a tracked input cannot be scanned. Its fixed outcome has already
        been published.

    Examples
    --------
    >>> find_and_publish(  # doctest: +SKIP
    ...     pathlib.Path("."), phrases.load_policy(pathlib.Path("."))
    ... ).findings
    ()
    """
    try:
        result = phrases.find_phrases(
            phrase_source.RepositoryTextSource(repository), policy
        )
    except phrases.PhraseScanError as error:
        publish_scan_failure(error)
        raise
    publish_scan_outcomes(result.outcomes)
    return result
