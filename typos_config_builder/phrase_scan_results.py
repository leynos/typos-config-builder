"""Carry fixed outcomes across the tracked phrase-file boundary."""

from __future__ import annotations

import dataclasses as dc
import enum


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
    is_eligible
        Whether the phrase scan may read this path.
    fails_scan
        Whether this result must abort the phrase scan.
    """

    ELIGIBLE = ("eligible", "")
    SYMLINK = ("skipped-symlink", "symlink")
    OUTSIDE_WORKTREE = ("skipped-outside-worktree", "containment")
    NON_REGULAR = ("skipped-non-regular", "non-regular")
    OVERSIZED = ("skipped-oversized", "size-limit")
    DIRECTORY_REPLACEMENT = ("failed-directory-replacement", "directory")

    @property
    def decision(self) -> str:
        """The fixed log category for this eligibility result."""
        return self.value[0]

    @property
    def error_class(self) -> str:
        """The fixed reason label for this eligibility result."""
        return self.value[1]

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

    Attributes
    ----------
    decision
        Fixed phrase-scan log category for this result.
    error_class
        Fixed reason label paired with the category.
    """

    TEXT = ("scanned-text", "utf-8")
    OVERSIZED = ("skipped-oversized", "size-limit")
    BINARY = ("skipped-binary", "not-utf-8")

    @property
    def decision(self) -> str:
        """The fixed log category for this read result."""
        return self.value[0]

    @property
    def error_class(self) -> str:
        """The fixed reason label for this read result."""
        return self.value[1]


@dc.dataclass(frozen=True, slots=True)
class TrackedTextRead:
    """Carry decoded text and its fixed outcome to phrase-scan orchestration.

    Attributes
    ----------
    text
        Decoded text when the read outcome is ``TEXT``; otherwise ``None``.
    outcome
        ``TEXT`` when ``text`` is present, ``OVERSIZED`` or ``BINARY`` when
        ``text`` is absent.

    Examples
    --------
    >>> TrackedTextRead(None, ReadOutcome.BINARY).outcome.decision
    'skipped-binary'
    """

    text: str | None
    outcome: ReadOutcome
