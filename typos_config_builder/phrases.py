"""Enforce shared phrase corrections that Typos cannot express.

Typos tokenizes on punctuation, so a hyphenated prohibited phrase never
reaches its dictionary. The shared authority carries such phrases in
``[phrases.corrections]``, and this module applies them to a repository's
tracked UTF-8 text using the same ignore patterns and file exclusions as the
generated configuration.

The scan fails closed: a tracked file that cannot be read or decoded raises
rather than being skipped, because a silent skip hides exactly the files most
likely to have drifted.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

import pathspec

from typos_config_builder import builder
from typos_config_builder import patterns as pattern_policy
from typos_config_builder import policy as policy_document
from typos_config_builder.phrases_files import (
    POLICY_PATHS,
    PhraseScanError,
    is_scannable,
    read_tracked_text,
    tracked_files,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

# Tracked-file selection lives in :mod:`typos_config_builder.phrases_files`,
# and is re-exported here so callers keep one import site for the phrase gate.
__all__ = [
    "POLICY_PATHS",
    "PhraseFinding",
    "PhrasePolicy",
    "PhraseScanError",
    "find_phrases",
    "load_policy",
    "mask",
    "scan_text",
    "tracked_files",
]

_MARKED = 1


@dc.dataclass(frozen=True, slots=True)
class PhraseFinding:
    """Describe one prohibited phrase found in tracked text.

    Attributes
    ----------
    path
        Repository-relative path containing the phrase.
    line
        One-based source line number.
    column
        One-based source column number.
    phrase
        Matched text preserving the case as written.
    correction
        Replacement prescribed by shared policy.
    """

    path: pathlib.Path
    line: int
    column: int
    phrase: str
    correction: str


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


def _blank_marked(text: str, patterns: cabc.Sequence[re.Pattern[str]]) -> str:
    """Mark every ignored span against the original text, then blank it once.

    Blanking each pattern in turn would destroy delimiters a later pattern
    needs, so overlapping spans would only be partly ignored.
    """
    if not patterns:
        return text
    marked = bytearray(len(text))
    for pattern in patterns:
        for match in pattern.finditer(text):
            marked[match.start() : match.end()] = bytes(
                [_MARKED] * (match.end() - match.start())
            )
    return "".join(
        "\n" if character == "\n" else " " if marked[index] else character
        for index, character in enumerate(text)
    )


def mask(text: str, patterns: cabc.Sequence[str]) -> str:
    r"""Blank ignored spans while preserving every line and column.

    Parameters
    ----------
    text
        Original source text.
    patterns
        Bounded ignore expressions validated by :mod:`typos_config_builder.patterns`.

    Returns
    -------
    str
        Text of identical length with ignored characters replaced by spaces
        and newlines preserved.

    Raises
    ------
    ValueError
        If an ignore expression is invalid or prone to backtracking.

    Examples
    --------
    >>> mask("say `noes` now", (r"`[^`\n]+`",))
    'say        now'
    """
    return _blank_marked(
        text, tuple(pattern_policy.compile_pattern(pattern) for pattern in patterns)
    )


@dc.dataclass(frozen=True, slots=True)
class _Scanner:
    """Hold the compiled masking and phrase matchers used for one run."""

    ignore_patterns: tuple[re.Pattern[str], ...]
    matchers: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def from_policy(cls, policy: PhrasePolicy) -> _Scanner:
        """Compile ignore expressions and phrase boundaries once per run."""
        return cls(
            ignore_patterns=tuple(
                pattern_policy.compile_pattern(pattern)
                for pattern in policy.ignore_patterns
            ),
            matchers=tuple(
                (
                    correction,
                    re.compile(
                        rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", re.IGNORECASE
                    ),
                )
                for phrase, correction in policy.phrase_corrections
            ),
        )

    def scan(self, path: pathlib.Path, text: str) -> tuple[PhraseFinding, ...]:
        """Return findings for one unit of text, ordered by policy then position."""
        masked = _blank_marked(text, self.ignore_patterns)
        findings: list[PhraseFinding] = []
        for correction, matcher in self.matchers:
            for match in matcher.finditer(masked):
                start = match.start()
                findings.append(
                    PhraseFinding(
                        path=path,
                        line=masked.count("\n", 0, start) + 1,
                        column=start - masked.rfind("\n", 0, start),
                        phrase=text[start : match.end()],
                        correction=correction,
                    )
                )
        return tuple(findings)


def scan_text(
    path: pathlib.Path, text: str, policy: PhrasePolicy
) -> tuple[PhraseFinding, ...]:
    """Return prohibited phrases in one unit of text.

    Parameters
    ----------
    path
        Repository-relative path reported by each finding.
    text
        Source text to scan.
    policy
        Phrase corrections and ignore expressions to apply.

    Returns
    -------
    tuple[PhraseFinding, ...]
        Findings ordered by policy order then source position.

    Raises
    ------
    ValueError
        If an ignore expression is invalid or prone to backtracking.

    Examples
    --------
    >>> policy = PhrasePolicy((("fit-for-purpose", "suitable"),), (), ())
    >>> scan_text(pathlib.Path("README.md"), "is fit-for-purpose", policy)[0].column
    4
    """
    return _Scanner.from_policy(policy).scan(path, text)


def find_phrases(
    repository: pathlib.Path, policy: PhrasePolicy
) -> tuple[PhraseFinding, ...]:
    """Find prohibited phrases across a repository's tracked text.

    Policy documents are never scanned, and excluded paths are skipped using
    gitignore semantics. A tracked path is skipped when it is a symlink, when
    it resolves outside the worktree through a symlinked parent, or when it is
    a submodule gitlink, which is a directory rather than text.

    Parameters
    ----------
    repository
        Git worktree whose tracked files should be scanned.
    policy
        Merged phrase corrections, ignore expressions, and exclusions.

    Returns
    -------
    tuple[PhraseFinding, ...]
        Findings ordered by tracked path, then policy order, then position.

    Raises
    ------
    FileNotFoundError
        If ``git`` is not available on the executable search path.
    PhraseScanError
        If a tracked file cannot be read or decoded as UTF-8.
    subprocess.CalledProcessError
        If ``git`` cannot enumerate the repository's tracked files.
    ValueError
        If an ignore expression is invalid or prone to backtracking.

    Examples
    --------
    >>> repository = pathlib.Path(".")
    >>> find_phrases(repository, load_policy(repository))  # doctest: +SKIP
    ()
    """
    scanner = _Scanner.from_policy(policy)
    exclusions = pathspec.GitIgnoreSpec.from_lines(policy.excluded_files)
    root = repository.resolve()
    findings: list[PhraseFinding] = []
    for relative in tracked_files(repository):
        if relative in POLICY_PATHS or exclusions.match_file(relative.as_posix()):
            continue
        candidate = repository / relative
        if not is_scannable(candidate, root):
            continue
        findings.extend(scanner.scan(relative, read_tracked_text(candidate, relative)))
    return tuple(findings)
