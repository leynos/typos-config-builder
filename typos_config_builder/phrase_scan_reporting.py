"""Publish path-free phrase-scan diagnostics and bounded outcome metrics.

The phrase query returns typed outcomes and never logs. Command-level code
calls this module to publish them, so a library caller can run the scan
without inheriting an observability side effect.

Metrics use fixed labels from :mod:`typos_config_builder.phrase_scan_results`;
tracked paths, contents, and raw error text never become labels. They are
written as ``metric name{reason=...}=count`` lines on standard error, the
machine-readable channel Continuous Integration (CI) logs already collect.
"""

from __future__ import annotations

import logging
import sys
import typing as typ

from typos_config_builder import phrases
from typos_config_builder.phrase_scan_results import ScanOutcome, metric_counts
from typos_config_builder.phrases_files import PhraseScanError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

LOGGER = logging.getLogger(__name__)

#: Skipped inputs, counted per fixed reason label.
SKIPPED_METRIC = "phrase_scan_files_skipped_total"

#: Fail-closed inputs, counted per fixed reason label.
FAILED_METRIC = "phrase_scan_files_failed_total"


def _log_outcome(outcome: ScanOutcome) -> None:
    """Log one fixed outcome without exposing a tracked path or content."""
    LOGGER.debug(
        "Phrase scan decision",
        extra={
            "operation": "phrase-scan",
            "decision": outcome.decision,
            "error_class": outcome.error_class,
        },
    )


def _emit_metric(name: str, reason: str, count: int) -> None:
    """Write one bounded metric line for a log-based collector."""
    sys.stderr.write(f"metric {name}{{reason={reason}}}={count}\n")


def _emit_counts(name: str, outcomes: cabc.Iterable[ScanOutcome]) -> None:
    """Write one metric line per fixed reason that occurred."""
    for reason, count in sorted(metric_counts(outcomes).items()):
        _emit_metric(name, reason, count)


def publish_scan_outcomes(outcomes: cabc.Iterable[ScanOutcome]) -> None:
    """Publish fixed diagnostics and skip metrics for a completed scan.

    Parameters
    ----------
    outcomes
        Fixed skip and read outcomes returned by the phrase query.

    Examples
    --------
    >>> publish_scan_outcomes(())  # no skips means no metric line
    """
    collected = tuple(outcomes)
    for outcome in collected:
        _log_outcome(outcome)
    _emit_counts(SKIPPED_METRIC, collected)


def publish_scan_failure(error: PhraseScanError) -> None:
    """Publish the fixed diagnostic and failure metric for an aborted scan.

    Parameters
    ----------
    error
        The fail-closed error raised by the phrase query.

    Examples
    --------
    >>> publish_scan_failure(  # doctest: +SKIP
    ...     PhraseScanError(pathlib.Path("README.md"), ScanFailure.READ)
    ... )
    """
    _log_outcome(error.outcome)
    _emit_counts(FAILED_METRIC, (error.outcome,))


def find_and_publish(
    repository: pathlib.Path, policy: phrases.PhrasePolicy
) -> phrases.PhraseScanResult:
    """Run the phrase query and publish whatever it returns.

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
    PhraseScanError
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
        result = phrases.find_phrases(repository, policy)
    except PhraseScanError as error:
        publish_scan_failure(error)
        raise
    publish_scan_outcomes(result.outcomes)
    return result
