"""Carry fixed filesystem categories across the tracked phrase-file boundary.

The enums here describe what the filesystem layer observed. They carry no log
or metric labels: :mod:`typos_config_builder.phrase_scan_reporting` owns the
mapping from a category to the text a command publishes. The dataclasses pair
a category with the decoded content, if any, so callers never have to infer
one from the other.
"""

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
    is_eligible
        Whether the phrase scan may read this path.
    fails_scan
        Whether this result must abort the phrase scan.
    """

    ELIGIBLE = enum.auto()
    SYMLINK = enum.auto()
    OUTSIDE_WORKTREE = enum.auto()
    NON_REGULAR = enum.auto()
    OVERSIZED = enum.auto()
    DIRECTORY_REPLACEMENT = enum.auto()

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
    """

    TEXT = enum.auto()
    OVERSIZED = enum.auto()
    BINARY = enum.auto()
    NON_REGULAR = enum.auto()


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
    """

    METADATA = enum.auto()
    READ = enum.auto()
    DIRECTORY_REPLACEMENT = enum.auto()


@dc.dataclass(frozen=True, slots=True)
class ScannableText:
    """Carry decoded text or a fixed skip category from the read boundary.

    Exactly one attribute is populated: decoded text when the path was read,
    or the fixed category explaining why no text was produced.

    Attributes
    ----------
    text
        Decoded UTF-8 text when the tracked path was read; otherwise ``None``.
    outcome
        The fixed skip category when no text was read; otherwise ``None``.

    Examples
    --------
    >>> ScannableText(None, ReadOutcome.BINARY).outcome is ReadOutcome.BINARY
    True
    """

    text: str | None
    outcome: ScanEligibility | ReadOutcome | None


@dc.dataclass(frozen=True, slots=True)
class TrackedTextRead:
    """Carry decoded text and its fixed category to phrase-scan orchestration.

    Attributes
    ----------
    text
        Decoded text when the read outcome is ``TEXT``; otherwise ``None``.
    outcome
        ``TEXT`` when ``text`` is present; otherwise an oversized, binary, or
        non-regular read outcome.

    Examples
    --------
    >>> TrackedTextRead("text", ReadOutcome.TEXT).text
    'text'
    >>> TrackedTextRead(None, ReadOutcome.BINARY).outcome is ReadOutcome.BINARY
    True
    """

    text: str | None
    outcome: ReadOutcome
