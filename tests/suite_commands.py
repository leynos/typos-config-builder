"""Recognize shell commands that run the test suite.

The suite-once contract needs to know whether a workflow line runs the suite
outside the coverage action. A substring check is both too wide (``echo
pytest``) and too narrow (``make "test"``, ``make lint&&make test``), so a
command is split into segments at shell separators, each segment is split
into words as the shell would, and the program each segment runs is read
before its arguments.
"""

from __future__ import annotations

import re
import shlex

#: Make options that take their value as the next word.
MAKE_VALUE_OPTIONS = frozenset({
    "-C",
    "-f",
    "-I",
    "-o",
    "-W",
    "--directory",
    "--file",
    "--makefile",
})
#: Make targets that run the suite. A bare ``make`` runs the default goal,
#: ``all``, which depends on ``test``, so it counts too.
SUITE_TARGETS = frozenset({"test", "all"})
#: ``uv run`` options that take their value as the next word.
UV_VALUE_OPTIONS = frozenset({
    "--with",
    "--python",
    "-p",
    "--group",
    "--extra",
    "--from",
    "--project",
})
#: Separators between commands on one line, spaced or not.
SEPARATORS = re.compile(r"[;|&\n]")
#: Programs that are pytest itself.
PYTEST_PROGRAMS = frozenset({"pytest", "py.test"})


def _words(segment: str) -> list[str]:
    """Split one segment into words as the shell would, less assignments."""
    try:
        words = shlex.split(segment, comments=True)
    except ValueError:
        words = segment.split()
    while words and "=" in words[0] and not words[0].startswith("-"):
        words = words[1:]
    return words


def _operands(words: list[str], value_options: frozenset[str]) -> list[str]:
    """Return a command's operands: its words less options and their values."""
    found: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in value_options:
            skip = True
        elif not word.startswith("-") and "=" not in word:
            found.append(word)
    return found


def _unwrap(words: list[str]) -> list[str]:
    """Strip a ``uv run``, ``uvx`` or ``python -m`` wrapper from a command."""
    program = words[0].rsplit("/", 1)[-1] if words else ""
    if program == "uv" and words[1:2] == ["run"]:
        return _unwrap(_after_options(words[2:], UV_VALUE_OPTIONS))
    if program == "uvx":
        return _unwrap(_after_options(words[1:], UV_VALUE_OPTIONS))
    if program in {"python", "python3"} and words[1:2] == ["-m"]:
        return words[2:]
    return words


def _after_options(words: list[str], value_options: frozenset[str]) -> list[str]:
    """Return the words from the first operand on."""
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in value_options else 1
    return words[index:]


def _segment_runs_suite(segment: str) -> bool:
    """Report whether one shell segment runs the suite."""
    words = _unwrap(_words(segment))
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1]
    if program in PYTEST_PROGRAMS:
        return True
    if program != "make":
        return False
    targets = _operands(words[1:], MAKE_VALUE_OPTIONS)
    return not targets or bool(SUITE_TARGETS & set(targets))


def runs_suite(command: str) -> bool:
    """Report whether a shell command runs the suite, in any spelling.

    Parameters
    ----------
    command : str
        A workflow step's ``run`` text, which may span several lines.

    Returns
    -------
    bool
        True when any segment runs pytest, or runs ``make`` with no target,
        ``test`` or ``all``.

    Examples
    --------
    >>> runs_suite("make lint&&make test")
    True
    >>> runs_suite("echo pytest")
    False
    """
    return any(_segment_runs_suite(part) for part in SEPARATORS.split(command))
