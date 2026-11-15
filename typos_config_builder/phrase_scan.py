"""Read eligible tracked text without publishing diagnostics.

This module is a query boundary: it classifies a tracked path, reads bounded
UTF-8 text when the path is eligible, and returns a typed outcome. It never
logs, so callers can decide how and where to publish diagnostics and metrics.
"""

from __future__ import annotations

import typing as typ

from typos_config_builder import phrases_files
from typos_config_builder.phrase_scan_results import (
    ScanEligibility,
    ScanFailure,
    ScannableText,
)
from typos_config_builder.phrases_files import PhraseScanError

if typ.TYPE_CHECKING:
    import pathlib


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
        raise PhraseScanError(relative, ScanFailure.METADATA) from error


def read_scannable_text(
    candidate: pathlib.Path,
    relative: pathlib.Path,
    root: pathlib.Path,
) -> ScannableText:
    """Read tracked UTF-8 text when a path is safe for phrase scanning.

    The result carries either decoded text or the fixed outcome that explains
    why the path produced none, leaving diagnostics to the command layer.

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
    ScannableText
        Decoded text for an eligible regular file, or the fixed skip outcome
        for an ineligible, oversized, binary, or non-regular input.

    Raises
    ------
    PhraseScanError
        If metadata or content cannot be read, or a tracked entry has been
        replaced by a directory. The error carries a fixed failure category.

    Examples
    --------
    Read a small tracked Markdown file before scanning its text::

        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     root = Path(directory).resolve()
        ...     candidate = root / "README.md"
        ...     _ = candidate.write_text("Hello", encoding="utf-8")
        ...     read_scannable_text(candidate, Path("README.md"), root).text
        'Hello'
    """
    eligibility = _scan_eligibility_for_path(candidate, relative, root)
    if not eligibility.is_eligible:
        if eligibility.fails_scan:
            raise PhraseScanError(relative, ScanFailure.DIRECTORY_REPLACEMENT)
        return ScannableText(None, eligibility)
    tracked_text = phrases_files.read_tracked_text(root, relative)
    if tracked_text.text is not None:
        return ScannableText(tracked_text.text, None)
    return ScannableText(None, tracked_text.outcome)
