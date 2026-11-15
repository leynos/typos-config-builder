"""Carry fixed outcomes across the tracked phrase-file boundary."""

from __future__ import annotations

import dataclasses as dc
import enum
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class ScanEligibility(enum.Enum):
    """Describe whether a tracked path may be read by the phrase scan.

    Members
    -------
    ELIGIBLE
        A regular file within the worktree and the optional size limit.
    SYMLINK
        A symbolic link that must not be followed.
    OUTSIDE_WORKTREE
        A path whose resolved target escapes the repository.
    NON_REGULAR
        A FIFO, device, socket, or other non-regular filesystem entry.
    OVERSIZED
        A regular file that exceeds the configured phrase-scan limit.
    DIRECTORY_REPLACEMENT
        A directory at a path that Git enumerated as a file.

    Attributes
    ----------
    decision
        Fixed phrase-scan log category for this result.
    error_class
        Fixed reason label paired with the category.
    metric_reason
        Fixed metric label for a skipped path, or ``None`` when eligible.
    is_eligible
        Whether the phrase scan may read this path.
    fails_scan
        Whether this result must abort the phrase scan.
    """

    ELIGIBLE = ("eligible", "", None)
    SYMLINK = ("skipped-symlink", "symlink", "symlink")
    OUTSIDE_WORKTREE = ("skipped-outside-worktree", "containment", "outside-worktree")
    NON_REGULAR = ("skipped-non-regular", "non-regular", "non-regular")
    OVERSIZED = ("skipped-oversized", "size-limit", "oversized")
    DIRECTORY_REPLACEMENT = (
        "failed-directory-replacement",
        "directory",
        "directory-replacement",
    )

    @property
    def decision(self) -> str:
        """The fixed log category for this eligibility result."""
        return self.value[0]

    @property
    def error_class(self) -> str:
        """The fixed reason label for this eligibility result."""
        return self.value[1]

    @property
    def metric_reason(self) -> str | None:
        """The fixed metric label for a skipped path, or ``None``."""
        return self.value[2]

    @property
    def is_eligible(self) -> bool:
        """Report whether a phrase scan may read the tracked path."""
        return self is ScanEligibility.ELIGIBLE

    @property
    def fails_scan(self) -> bool:
        """Report whether this result must abort the phrase scan."""
        return self is ScanEligibility.DIRECTORY_REPLACEMENT


class ReadOutcome(enum.Enum):
    """Describe the result of a bounded tracked-file read.

    Members
    -------
    TEXT
        The file was within the size limit and decoded as UTF-8.
    OVERSIZED
        The bounded read found at least one byte beyond the size limit.
    BINARY
        The bounded contents could not be decoded as UTF-8.
    NON_REGULAR
        The opened descriptor no longer names a regular file.

    Attributes
    ----------
    decision
        Fixed phrase-scan log category for this result.
    error_class
        Fixed reason label paired with the category.
    metric_reason
        Fixed metric label for a skipped input, or ``None`` when text was read.
    """

    TEXT = ("scanned-text", "utf-8", None)
    OVERSIZED = ("skipped-oversized", "size-limit", "oversized")
    BINARY = ("skipped-binary", "not-utf-8", "binary")
    NON_REGULAR = ("skipped-non-regular", "non-regular", "non-regular")

    @property
    def decision(self) -> str:
        """The fixed log category for this read result."""
        return self.value[0]

    @property
    def error_class(self) -> str:
        """The fixed reason label for this read result."""
        return self.value[1]

    @property
    def metric_reason(self) -> str | None:
        """The fixed metric label for a skipped input, or ``None``."""
        return self.value[2]


class ScanFailure(enum.Enum):
    """Describe a fail-closed phrase-scan outcome.

    Members
    -------
    METADATA
        The tracked path's filesystem metadata could not be read.
    READ
        An eligible tracked file could not be opened or read.
    DIRECTORY_REPLACEMENT
        A directory was found where Git enumerated a file.

    Attributes
    ----------
    decision
        Fixed phrase-scan log category for this failure.
    error_class
        Fixed reason label paired with the category.
    metric_reason
        Fixed metric label for this failure.
    """

    METADATA = ("failed-metadata", "file-system", "metadata-failure")
    READ = ("failed-read", "file-system", "read-failure")
    DIRECTORY_REPLACEMENT = (
        "failed-directory-replacement",
        "directory",
        "directory-replacement",
    )

    @property
    def decision(self) -> str:
        """The fixed log category for this failure."""
        return self.value[0]

    @property
    def error_class(self) -> str:
        """The fixed reason label for this failure."""
        return self.value[1]

    @property
    def metric_reason(self) -> str:
        """The fixed metric label for this failure."""
        return self.value[2]


#: Every fixed outcome the phrase boundary can report to the command layer.
ScanOutcome = ScanEligibility | ReadOutcome | ScanFailure


@dc.dataclass(frozen=True, slots=True)
class ScannableText:
    """Carry decoded text or a fixed skip outcome from the read boundary.

    Exactly one attribute is populated: decoded text when the path was read,
    or the fixed outcome explaining why no text was produced.

    Attributes
    ----------
    text
        Decoded UTF-8 text when the tracked path was read; otherwise ``None``.
    outcome
        The fixed skip outcome when no text was read; otherwise ``None``.

    Examples
    --------
    >>> ScannableText(None, ReadOutcome.BINARY).outcome.decision
    'skipped-binary'
    """

    text: str | None
    outcome: ScanOutcome | None


@dc.dataclass(frozen=True, slots=True)
class TrackedTextRead:
    """Carry decoded text and its fixed outcome to phrase-scan orchestration.

    Attributes
    ----------
    text
        Decoded text when the read outcome is ``TEXT``; otherwise ``None``.
    outcome
        ``TEXT`` when ``text`` is present; otherwise an oversized, binary, or
        non-regular read outcome.

    Examples
    --------
    >>> TrackedTextRead(None, ReadOutcome.BINARY).outcome.decision
    'skipped-binary'
    """

    text: str | None
    outcome: ReadOutcome


def metric_counts(outcomes: cabc.Iterable[ScanOutcome]) -> dict[str, int]:
    """Count outcomes by their fixed metric label.

    Only outcomes that name a skip or a failure contribute a label, so a
    successful read never inflates the skipped-input counter.

    Parameters
    ----------
    outcomes
        Fixed outcomes collected by a phrase scan.

    Returns
    -------
    dict[str, int]
        Count per fixed metric label, in first-seen order. Labels are stable
        and carry no path, content, or raw error text.

    Examples
    --------
    >>> metric_counts((ReadOutcome.BINARY, ReadOutcome.BINARY))
    {'binary': 2}
    """
    counts: dict[str, int] = {}
    for outcome in outcomes:
        reason = outcome.metric_reason
        if reason is None:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return counts
