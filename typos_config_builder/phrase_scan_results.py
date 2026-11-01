"""Carry fixed outcomes across the tracked phrase-file boundary."""

from __future__ import annotations

import dataclasses as dc
import enum


class ScanEligibility(enum.Enum):
    """Describe a tracked path's eligibility without producing diagnostics.

    Each rejected result carries a fixed decision and reason for the phrase
    scan's orchestration layer to log without repeating filesystem checks.
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


class ReadOutcome(enum.Enum):
    """Describe whether a bounded tracked-file read produced UTF-8 text."""

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
    """Carry read text and its fixed outcome to the phrase-scan boundary."""

    text: str | None
    outcome: ReadOutcome
