"""Adapt Git enumeration and bounded reads to the phrase domain port.

This module is the only place that knows both the domain vocabulary in
:mod:`typos_config_builder.phrases` and the filesystem vocabulary in
:mod:`typos_config_builder.phrase_scan` and
:mod:`typos_config_builder.phrase_scan_results`. It enumerates the worktree's
tracked paths and reads the ones the domain selects, translates every
filesystem skip category into a domain
:class:`~typos_config_builder.phrases.SkipReason`, and re-raises a read failure
as :class:`~typos_config_builder.phrases.PhraseScanError` so the query never
imports the filesystem layer.
"""

from __future__ import annotations

import typing as typ

from typos_config_builder import phrase_scan, phrases, phrases_files
from typos_config_builder.phrase_scan_results import (
    ReadOutcome,
    ScanEligibility,
    ScanFailure,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

#: Filesystem skip categories that mean "no text for the phrase query".
_SKIP_REASONS: dict[ScanEligibility | ReadOutcome, phrases.SkipReason] = {
    ScanEligibility.SYMLINK: phrases.SkipReason.SYMLINK,
    ScanEligibility.OUTSIDE_WORKTREE: phrases.SkipReason.OUTSIDE_WORKTREE,
    ScanEligibility.NON_REGULAR: phrases.SkipReason.NON_REGULAR,
    ScanEligibility.OVERSIZED: phrases.SkipReason.OVERSIZED,
    ReadOutcome.NON_REGULAR: phrases.SkipReason.NON_REGULAR,
    ReadOutcome.OVERSIZED: phrases.SkipReason.OVERSIZED,
    ReadOutcome.BINARY: phrases.SkipReason.BINARY,
}

#: Filesystem failures that must fail the phrase scan closed.
_FAILURE_REASONS: dict[ScanFailure, phrases.ScanFailureReason] = {
    ScanFailure.METADATA: phrases.ScanFailureReason.METADATA,
    ScanFailure.READ: phrases.ScanFailureReason.READ,
    ScanFailure.DIRECTORY_REPLACEMENT: phrases.ScanFailureReason.DIRECTORY_REPLACEMENT,
}


def _skip_reason(
    outcome: ScanEligibility | ReadOutcome | None,
) -> phrases.SkipReason:
    """Translate a filesystem skip category into its domain reason.

    Parameters
    ----------
    outcome
        Read-boundary category that produced no text.

    Returns
    -------
    phrases.SkipReason
        Domain reason the input contributed no text.

    Raises
    ------
    ValueError
        If the category has no domain reason, including ``None``.
    """
    if outcome is None:
        message = "phrase scan produced neither text nor a skip outcome"
        raise ValueError(message)
    try:
        return _SKIP_REASONS[outcome]
    except KeyError:
        message = f"unmapped phrase-scan skip outcome: {outcome!r}"
        raise ValueError(message) from None


def _failure_reason(outcome: ScanFailure) -> phrases.ScanFailureReason:
    """Translate a filesystem failure into its domain reason.

    Parameters
    ----------
    outcome
        Read-boundary failure that aborted the scan.

    Returns
    -------
    phrases.ScanFailureReason
        Domain reason the scan failed closed.

    Raises
    ------
    ValueError
        If the failure category has no domain reason.
    """
    try:
        return _FAILURE_REASONS[outcome]
    except KeyError:
        message = f"unmapped phrase-scan failure outcome: {outcome!r}"
        raise ValueError(message) from None


class RepositoryTextSource:
    """Adapt a Git worktree to the phrase query's tracked-text port.

    Attributes
    ----------
    repository
        Worktree whose tracked paths are enumerated.
    root
        Resolved worktree every read must stay inside.
    """

    def __init__(self, repository: pathlib.Path) -> None:
        """Store the worktree whose tracked inputs are yielded."""
        self.repository = repository
        self.root = repository.resolve()

    def tracked_paths(self) -> cabc.Iterator[pathlib.Path]:
        """Yield every tracked path, in Git's deterministic order.

        Returns
        -------
        collections.abc.Iterator[pathlib.Path]
            Repository-relative paths, whether or not they are readable.

        Raises
        ------
        FileNotFoundError
            If ``git`` is not available on the executable search path.
        subprocess.CalledProcessError
            If ``git`` cannot enumerate the repository's tracked files.
        """
        return iter(phrases_files.tracked_files(self.repository))

    def read(self, relative: pathlib.Path) -> phrases.TrackedInput:
        """Read one tracked path and report it as a domain record.

        Parameters
        ----------
        relative
            Repository-relative path selected by the query's policy.

        Returns
        -------
        phrases.TrackedInput
            Decoded text, or the domain reason the path contributed none.

        Raises
        ------
        phrases.PhraseScanError
            If the path cannot be read. The original filesystem error remains
            the cause.
        """
        return _tracked_input(self.root, relative)


def _tracked_input(root: pathlib.Path, relative: pathlib.Path) -> phrases.TrackedInput:
    """Read one tracked path and report it as a domain record."""
    try:
        scan = phrase_scan.read_scannable_text(root / relative, relative, root)
    except phrases_files.TrackedFileError as error:
        # Keep the underlying OSError as the direct cause when the filesystem
        # layer wrapped one, so a caller still sees why the read failed rather
        # than only the translated category.
        cause = error if error.__cause__ is None else error.__cause__
        raise phrases.PhraseScanError(
            relative, _failure_reason(error.outcome)
        ) from cause
    if scan.text is not None:
        return phrases.TrackedText(relative, scan.text)
    return phrases.SkippedInput(relative, _skip_reason(scan.outcome))
