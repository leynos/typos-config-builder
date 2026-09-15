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
import pathlib
import re
import shutil

# The phrase gate enumerates tracked files through Git by design; the
# command is resolved from PATH and its arguments are never user text.
import subprocess  # noqa: S404
import typing as typ

import pathspec

from typos_config_builder import builder
from typos_config_builder import patterns as pattern_policy
from typos_config_builder import policy as policy_document

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Policy documents describe prohibited phrases and must never be scanned for
#: them, otherwise the policy itself becomes a finding.
POLICY_PATHS = frozenset({
    pathlib.Path(builder.CACHE_NAME),
    pathlib.Path(builder.METADATA_NAME),
    pathlib.Path(builder.OVERLAY_NAME),
    pathlib.Path(builder.OUTPUT_NAME),
})

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


class PhraseScanError(Exception):
    """Report that a tracked file could not be read or decoded.

    Attributes
    ----------
    path
        Repository-relative path that could not be scanned.
    """

    def __init__(self, path: pathlib.Path) -> None:
        """Record the tracked path that could not be scanned.

        Parameters
        ----------
        path
            Repository-relative path that could not be read or decoded.
        """
        self.path = path
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


def tracked_files(repository: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Return a repository's Git-tracked paths in deterministic order.

    Parameters
    ----------
    repository
        Git worktree to enumerate.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Sorted repository-relative tracked paths.

    Raises
    ------
    FileNotFoundError
        If ``git`` is not available on the executable search path.
    subprocess.CalledProcessError
        If ``git`` cannot enumerate the repository's tracked files.

    Examples
    --------
    >>> tracked_files(pathlib.Path("."))  # doctest: +SKIP
    (PosixPath('README.md'),)
    """
    executable = shutil.which("git")
    if executable is None:
        message = "git was not found on PATH; it is required to list tracked files"
        raise FileNotFoundError(message)
    listing = subprocess.run(  # noqa: S603
        [executable, "-C", str(repository), "ls-files", "-z"],
        check=True,
        capture_output=True,
        # Git does not read standard input here, but a command double standing
        # in for it might. A shim waiting on an inherited terminal would wedge
        # the gate rather than fail it.
        stdin=subprocess.DEVNULL,
        text=True,
    ).stdout
    return tuple(
        pathlib.Path(relative) for relative in sorted(filter(None, listing.split("\0")))
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


def _read_tracked_text(path: pathlib.Path, relative: pathlib.Path) -> str:
    """Read tracked UTF-8 text, failing closed on any read or decode error."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PhraseScanError(relative) from error


def find_phrases(
    repository: pathlib.Path, policy: PhrasePolicy
) -> tuple[PhraseFinding, ...]:
    """Find prohibited phrases across a repository's tracked text.

    Policy documents are never scanned, excluded paths are skipped using
    gitignore semantics, and tracked symlinks are skipped so the scan cannot
    follow a link out of the repository.

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
    findings: list[PhraseFinding] = []
    for relative in tracked_files(repository):
        if relative in POLICY_PATHS or exclusions.match_file(relative.as_posix()):
            continue
        candidate = repository / relative
        if candidate.is_symlink():
            continue
        findings.extend(scanner.scan(relative, _read_tracked_text(candidate, relative)))
    return tuple(findings)
