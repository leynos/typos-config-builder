"""Own the phrase-checking domain: policy, inputs, findings, and the query.

A phrase scan is a query over a repository's tracked text. Policy documents
contribute corrections, ignore expressions, and exclusions; every other tracked
input either yields decoded text or a domain reason for contributing none; and
the pure matcher in :mod:`typos_config_builder.phrase_matching` reports the
prohibited phrases it finds.

The domain never enumerates Git or reads a file. It asks an injected
:class:`TrackedTextSource` first for tracked paths and then for the text of the
ones its policy selects, so a caller can supply a real worktree adapter or an
in-memory double. Enumeration and reading stay separate operations so a policy
exclusion is applied before any file is opened. The domain publishes no
diagnostics or metrics either: :mod:`typos_config_builder.phrase_scan_reporting`
owns those at the command boundary.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import typing as typ

import pathspec

from typos_config_builder import builder
from typos_config_builder import policy as policy_document
from typos_config_builder.phrase_matching import (
    PhraseFinding,
    Scanner,
    mask,
    scan_text,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

__all__ = [
    "POLICY_PATHS",
    "PhraseFinding",
    "PhrasePolicy",
    "PhraseScanError",
    "PhraseScanResult",
    "ScanFailureReason",
    "SkipReason",
    "SkippedInput",
    "TrackedInput",
    "TrackedText",
    "TrackedTextSource",
    "find_phrases",
    "load_policy",
    "mask",
    "scan_text",
]

#: Repository-relative POSIX spellings of documents that describe prohibited
#: phrases. Scanning one would report the policy itself as a finding, so the
#: query skips them before it asks the source for text.
POLICY_PATHS = frozenset({
    builder.CACHE_NAME,
    builder.METADATA_NAME,
    builder.OVERLAY_NAME,
    builder.OUTPUT_NAME,
})


@dc.dataclass(frozen=True, slots=True)
class PhraseScanResult:
    """Report findings and fixed skip reasons from one phrase scan.

    Attributes
    ----------
    findings
        Prohibited phrases found in tracked text, in source order, then
        policy order, then position.
    outcomes
        One domain skip reason per tracked input that contributed no text, in
        source order. A successful read contributes no outcome.
    """

    findings: tuple[PhraseFinding, ...]
    outcomes: tuple[SkipReason, ...]


@dc.dataclass(frozen=True, slots=True)
class PhrasePolicy:
    """Hold the merged policy the phrase scanner consumes.

    Attributes
    ----------
    phrase_corrections
        Prohibited phrase and replacement pairs in policy order.
    ignore_patterns
        Bounded regular expressions whose matches are masked before scanning.
    excluded_files
        Gitignore-style patterns for paths omitted from the scan.
    """

    phrase_corrections: tuple[tuple[str, str], ...]
    ignore_patterns: tuple[str, ...]
    excluded_files: tuple[str, ...]


class SkipReason(enum.Enum):
    """Explain why a tracked input contributed no text to the phrase query.

    Members
    -------
    SYMLINK
        The tracked entry is a symbolic link that must not be followed.
    OUTSIDE_WORKTREE
        The path resolves outside the worktree boundary.
    NON_REGULAR
        A FIFO, device, socket, or other non-regular filesystem entry.
    OVERSIZED
        The content exceeds the phrase-scan size limit.
    BINARY
        The content is not valid UTF-8, so it carries no prose to check.
    """

    SYMLINK = enum.auto()
    OUTSIDE_WORKTREE = enum.auto()
    NON_REGULAR = enum.auto()
    OVERSIZED = enum.auto()
    BINARY = enum.auto()


class ScanFailureReason(enum.Enum):
    """Explain why a phrase scan failed closed on a tracked input.

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
class TrackedText:
    """Hold decoded text read from one tracked path.

    Attributes
    ----------
    path
        Repository-relative path the text came from.
    text
        Decoded UTF-8 content ready for phrase matching.
    """

    path: pathlib.Path
    text: str


@dc.dataclass(frozen=True, slots=True)
class SkippedInput:
    """Report a tracked path that contributed no text, and why.

    Attributes
    ----------
    path
        Repository-relative path that was skipped.
    reason
        Domain reason the input contributed no text.
    """

    path: pathlib.Path
    reason: SkipReason


TrackedInput = TrackedText | SkippedInput


class TrackedTextSource(typ.Protocol):
    """Port enumerating a repository's tracked paths and reading their text.

    The two operations are separate so the query can drop a policy document or
    an excluded path before the source opens it. A policy exclusion therefore
    cannot fail a scan through a path the scan never reads.
    """

    def tracked_paths(self) -> cabc.Iterator[pathlib.Path]:
        """Yield every tracked path in a stable order."""
        ...

    def read(self, relative: pathlib.Path) -> TrackedInput:
        """Report one tracked path as text, or why it contributed none."""
        ...


class PhraseScanError(Exception):
    """Report that a tracked input could not be scanned.

    The failure carries a fixed reason so the command layer can publish a
    path-free diagnostic without inspecting the exception message.

    Attributes
    ----------
    path
        Repository-relative path that could not be scanned.
    reason
        Domain reason the scan failed closed.
    """

    def __init__(self, path: pathlib.Path, reason: ScanFailureReason) -> None:
        """Record the tracked path and the fixed failure reason."""
        self.path = path
        self.reason = reason
        super().__init__(f"tracked file could not be scanned: {path}")


def load_policy(repository: pathlib.Path) -> PhrasePolicy:
    """Load merged shared and overlay policy for phrase checking.

    Parameters
    ----------
    repository
        Repository holding the refreshed cache and optional overlay.

    Returns
    -------
    PhrasePolicy
        Effective phrase corrections, ignore patterns, and exclusions.

    Raises
    ------
    FileNotFoundError
        If the cache is absent, naming the command that creates it.
    OSError
        If a policy document cannot be read.
    ValueError
        If a policy document is malformed or conflicts with shared policy.

    Examples
    --------
    >>> policy = load_policy(pathlib.Path("."))  # doctest: +SKIP
    >>> policy.phrase_corrections  # doctest: +SKIP
    (('fit-for-purpose', 'suitable'),)
    """
    cache = repository / builder.CACHE_NAME
    if not cache.is_file():
        message = (
            f"{cache} is missing; run "
            f"'typos-config-builder --repository {repository}' first"
        )
        raise FileNotFoundError(message)
    dictionary = policy_document.load(cache)
    overlay = repository / builder.OVERLAY_NAME
    if overlay.exists():
        dictionary = policy_document.merge(
            dictionary, policy_document.load(overlay, sparse=True)
        )
    return PhrasePolicy(
        phrase_corrections=dictionary.phrase_corrections,
        ignore_patterns=dictionary.ignore_patterns,
        excluded_files=dictionary.excluded_files,
    )


def find_phrases(source: TrackedTextSource, policy: PhrasePolicy) -> PhraseScanResult:
    """Find prohibited phrases in the tracked inputs a source selects.

    The query applies the policy's ignore expressions and exclusions, skips
    policy documents so the rules are never matched against themselves, and
    reports one finding per prohibited phrase. Selection happens on the path
    alone, before the source is asked for any text, so an excluded or policy
    document is never opened and cannot fail the scan. The query reads no file
    and starts no process: the injected source owns enumeration and decoding,
    and reports an input it could not read as :class:`PhraseScanError`.

    Parameters
    ----------
    source
        Port enumerating tracked paths and reading the ones that survive
        selection.
    policy
        Merged phrase corrections, ignore expressions, and exclusions.

    Returns
    -------
    PhraseScanResult
        Findings in source order, then policy order, then position, together
        with one skip reason per selected input that contributed no text.

    Raises
    ------
    PhraseScanError
        Propagated unchanged when the source cannot read a selected input.
    ValueError
        If an ignore expression is invalid or prone to backtracking.

    Examples
    --------
    >>> source = empty_source  # doctest: +SKIP
    >>> find_phrases(source, PhrasePolicy((), (), ())).findings  # doctest: +SKIP
    ()
    """
    scanner = Scanner.from_policy(policy)
    exclusions = pathspec.GitIgnoreSpec.from_lines(policy.excluded_files)
    findings: list[PhraseFinding] = []
    outcomes: list[SkipReason] = []
    for relative in source.tracked_paths():
        if _is_unselected(relative, exclusions):
            continue
        record = source.read(relative)
        if isinstance(record, SkippedInput):
            outcomes.append(record.reason)
        else:
            findings.extend(scanner.scan(record.path, record.text))
    return PhraseScanResult(tuple(findings), tuple(outcomes))


def _is_unselected(relative: pathlib.Path, exclusions: pathspec.GitIgnoreSpec) -> bool:
    """Report whether policy drops a tracked path before it is read."""
    spelling = relative.as_posix()
    return spelling in POLICY_PATHS or exclusions.match_file(spelling)
