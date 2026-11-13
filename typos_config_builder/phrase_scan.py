"""Read eligible tracked text and log path-free phrase-scan outcomes."""

from __future__ import annotations

import logging
import typing as typ

from typos_config_builder import phrases_files
from typos_config_builder.phrases_files import PhraseScanError, read_tracked_text

if typ.TYPE_CHECKING:
    import pathlib

    from typos_config_builder.phrase_scan_results import ScanEligibility

LOGGER = logging.getLogger(__name__)


def _log_phrase_scan_decision(decision: str, error_class: str) -> None:
    """Log fixed outcomes such as ``('skipped-symlink', 'symlink')``."""
    LOGGER.debug(
        "Phrase scan decision",
        extra={
            "operation": "phrase-scan",
            "decision": decision,
            "error_class": error_class,
        },
    )


def _scan_eligibility_for_path(
    candidate: pathlib.Path,
    relative: pathlib.Path,
    root: pathlib.Path,
) -> ScanEligibility:
    """Classify a path, e.g. a symlink returns ``ScanEligibility.SYMLINK``."""
    try:
        return phrases_files.scan_eligibility(
            candidate,
            root,
            max_file_size_bytes=phrases_files.MAX_FILE_SIZE_BYTES,
        )
    except OSError as error:
        _log_phrase_scan_decision("failed-metadata", "file-system")
        raise PhraseScanError(relative) from error


def read_scannable_text(
    candidate: pathlib.Path,
    relative: pathlib.Path,
    root: pathlib.Path,
) -> str | None:
    """Read tracked UTF-8 text when a path is safe for phrase scanning.

    Parameters
    ----------
    candidate : pathlib.Path
        Path to the tracked entry on disk.
    relative : pathlib.Path
        Repository-relative path used in any raised scan error.
    root : pathlib.Path
        Resolved repository root used to check path containment.

    Returns
    -------
    str | None
        Decoded text for an eligible regular file, or ``None`` when the entry
        is ineligible, oversized, or not valid UTF-8.

    Raises
    ------
    PhraseScanError
        If metadata or content cannot be read, or a tracked entry has been
        replaced by a directory.

    Examples
    --------
    Read a small tracked Markdown file before scanning its text::

        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     root = Path(directory).resolve()
        ...     candidate = root / "README.md"
        ...     _ = candidate.write_text("Hello", encoding="utf-8")
        ...     read_scannable_text(candidate, Path("README.md"), root)
        'Hello'
    """
    eligibility = _scan_eligibility_for_path(candidate, relative, root)
    if not eligibility.is_eligible:
        _log_phrase_scan_decision(eligibility.decision, eligibility.error_class)
        if eligibility.fails_scan:
            raise PhraseScanError(relative) from IsADirectoryError(candidate)
        return None
    try:
        tracked_text = read_tracked_text(root, relative)
    except PhraseScanError:
        _log_phrase_scan_decision("failed-read", "file-system")
        raise
    if tracked_text.text is None:
        _log_phrase_scan_decision(
            tracked_text.outcome.decision,
            tracked_text.outcome.error_class,
        )
    return tracked_text.text
