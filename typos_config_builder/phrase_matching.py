"""Match prohibited phrases in decoded text without touching the filesystem.

This module is the pure half of the phrase gate. It compiles a policy's ignore
expressions and phrase boundaries once, masks ignored spans while preserving
every line and column, and reports each prohibited phrase it finds. It reads no
file, starts no process, and knows nothing about Git or worktree boundaries, so
its rules can be exercised over any string.
"""

from __future__ import annotations

import bisect
import dataclasses as dc
import re
import typing as typ

from typos_config_builder import patterns as pattern_policy

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from typos_config_builder import phrases

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


def _line_starts(text: str) -> tuple[int, ...]:
    """Return the offset at which each line of the text begins."""
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    return tuple(starts)


@dc.dataclass(frozen=True, slots=True)
class Scanner:
    """Hold the compiled masking and phrase matchers used for one run.

    Attributes
    ----------
    ignore_patterns
        Compiled ignore expressions whose matches are masked before scanning.
    matchers
        Compiled phrase boundary expressions paired with their correction.
    """

    ignore_patterns: tuple[re.Pattern[str], ...]
    matchers: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def from_policy(cls, policy: phrases.PhrasePolicy) -> Scanner:
        """Compile ignore expressions and phrase boundaries once per run.

        Parameters
        ----------
        policy
            Phrase corrections and ignore expressions to apply.

        Returns
        -------
        Scanner
            Matchers ready to scan any number of text units.
        """
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
        """Return findings for one unit of text, ordered by policy then position.

        Parameters
        ----------
        path
            Repository-relative path reported by each finding.
        text
            Source text to scan.

        Returns
        -------
        tuple[PhraseFinding, ...]
            Findings ordered by policy order then source position.
        """
        masked = _blank_marked(text, self.ignore_patterns)
        # Counting newlines per match rescans the text once per finding, which
        # is quadratic in a dense file. One pass over the text amortizes it.
        starts = _line_starts(masked)
        findings: list[PhraseFinding] = []
        for correction, matcher in self.matchers:
            for match in matcher.finditer(masked):
                start = match.start()
                line = bisect.bisect_right(starts, start)
                findings.append(
                    PhraseFinding(
                        path=path,
                        line=line,
                        column=start - starts[line - 1] + 1,
                        phrase=text[start : match.end()],
                        correction=correction,
                    )
                )
        return tuple(findings)


def scan_text(
    path: pathlib.Path, text: str, policy: phrases.PhrasePolicy
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
    >>> import pathlib
    >>> from typos_config_builder import phrases
    >>> policy = phrases.PhrasePolicy((("fit-for-purpose", "suitable"),), (), ())
    >>> scan_text(pathlib.Path("README.md"), "is fit-for-purpose", policy)[0].column
    4
    """
    return Scanner.from_policy(policy).scan(path, text)
