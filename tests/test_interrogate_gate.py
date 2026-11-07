"""Contract tests for the Python docstring-coverage gate in the Makefile.

`make lint` must reach an `interrogate` run over every Python directory the
repository owns (interrogate itself skips some path prefixes, so a new file
should appear in the verbose report), at a threshold of 100, with a guard that
fails on any `MISSED` row (interrogate rounds its percentage to one decimal, so
the threshold alone can pass a lone gap in a large repository). These tests read
the recipe and also run the target against a stub tool, so a deleted flag, a
dropped prerequisite or a guard that stops failing breaks a test rather than
silently restoring the gap.

Run via ``make test``.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs git and make with fixed argv
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"


def _makefile_text() -> str:
    """Return the Makefile source."""
    return MAKEFILE.read_text(encoding="utf-8")


def _variable(name: str) -> str:
    """Return the value a `NAME ?=` or `NAME =` assignment gives, joined."""
    pattern = re.compile(rf"^{name}\s*[:?]?=\s*((?:.*\\\n)*.*)$", re.MULTILINE)
    match = pattern.search(_makefile_text())
    assert match, f"the Makefile must assign {name}"
    return re.sub(r"\\\n\s*", " ", match.group(1)).strip()


def _expanded(name: str) -> str:
    """Return a variable's value with each `$(NAME)` reference expanded.

    Returns
    -------
    str
        The value with every referenced variable substituted, recursively.
    """
    value = _variable(name)
    while match := re.search(r"\$\((\w+)\)", value):
        value = value.replace(match.group(0), _variable(match.group(1)))
    return value


def _prerequisites(target: str) -> list[str]:
    """Return the prerequisites declared for *target*."""
    pattern = re.compile(rf"^{re.escape(target)}:([^=\n]*)$", re.MULTILINE)
    match = pattern.search(_makefile_text())
    assert match, f"the Makefile must declare a {target} target"
    return match.group(1).split("##")[0].split()


def _tracked(*patterns: str) -> list[str]:
    """Return the Git-tracked files matching *patterns*.

    Git is the authority on what the repository owns: a walk would also see
    the virtual environments CI creates beside the sources. Names are read from
    NUL-separated output, so a path containing whitespace stays whole.

    Returns
    -------
    list[str]
        The tracked paths, relative to the repository root.
    """
    output = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        ["git", "-C", str(ROOT), "ls-files", "-z", *patterns],  # ruff: ignore[start-process-with-partial-path] - git
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [name for name in output.split("\0") if name]


def _python_directories() -> set[Path]:
    """Return the directories that hold Python source or stubs Git tracks."""
    return {Path(name).parent for name in _tracked("*.py", "*.pyi")}


def test_lint_depends_on_interrogate() -> None:
    """Dropping the prerequisite would remove the gate from `make lint`."""
    assert "lint-python" in _prerequisites("lint"), "lint must run lint-python"
    assert "interrogate" in _prerequisites("lint-python")


def test_interrogate_is_declared_phony() -> None:
    """A file named `interrogate` must not make the target look up to date."""
    declared = re.findall(r"^\.PHONY:((?:.*\\\n)*.*)$", _makefile_text(), re.MULTILINE)
    names = " ".join(declared).replace("\\\n", " ").split()
    assert "interrogate" in names, "interrogate must be a .PHONY target"


def test_the_flags_demand_a_full_exact_report() -> None:
    """Exactly: every scanned definition printed and a threshold of 100.

    Equality, not a deny-list: any other flag (an ignore or exclude option, or
    `-q` and `-o`, which suppress or redirect the report the guard reads) could
    relax the gate or blind the MISSED guard.
    """
    flags = _variable("INTERROGATE_FLAGS").split()
    expected = ["-vv", "--fail-under", "100"]
    if _tracked("*.pyi"):
        expected = ["-vv", "--ext", "pyi", "--fail-under", "100"]
    assert flags == expected, (
        f"INTERROGATE_FLAGS must be exactly {expected}, not {flags}"
    )


def test_every_python_directory_is_a_target() -> None:
    """A Python directory outside `INTERROGATE_TARGETS` is not checked."""
    targets = [Path(t) for t in _expanded("INTERROGATE_TARGETS").split()]
    uncovered = [
        str(directory)
        for directory in sorted(_python_directories())
        if not any(directory.is_relative_to(target) for target in targets)
    ]
    assert not uncovered, f"add these to INTERROGATE_TARGETS: {uncovered}"


_MAKE_STATE = frozenset({
    "MAKEFLAGS",
    "MFLAGS",
    "GNUMAKEFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "UV",
})
# Interrogate 1.7.0 skips any path under a target whose name begins with one of
# these (virtual environments and version-control directories).
_EXCLUDED_PREFIXES = (".tox", ".venv", "venv", ".git", ".hg")


def test_no_tracked_file_hides_behind_a_default_exclusion() -> None:
    """A tracked file interrogate skips by default would never be measured."""
    targets = [Path(t) for t in _expanded("INTERROGATE_TARGETS").split()]
    hidden = [
        name
        for name in _tracked("*.py", "*.pyi")
        for target in targets
        if Path(name).is_relative_to(target)
        and Path(name) != target
        and any(
            part.startswith(_EXCLUDED_PREFIXES)
            for part in Path(name).relative_to(target).parts
        )
    ]
    assert not hidden, f"interrogate skips these by default: {hidden}"


def _run_target(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run `make interrogate` with the tool replaced by a stub script.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished `make` process with its captured output.
    """
    stub = tmp_path / "stub.sh"
    stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n'
        + textwrap.dedent(script),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    make = shutil.which("make")
    assert make, "make must be on PATH"
    # `-o build` treats the prerequisite as up to date: the stub needs no environment.
    # A parent Make's flags, a caller's UV or INTERROGATE_* overrides would change
    # what the stub receives, so the run starts from a clean environment.
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in _MAKE_STATE and not name.startswith("INTERROGATE")
    }
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [
            make,
            "-C",
            str(ROOT),
            "-o",
            "build",
            "interrogate",
            f"INTERROGATE={shlex.quote(str(stub))}",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_the_target_passes_when_the_tool_reports_no_gap(tmp_path: Path) -> None:
    """A clean report and a zero status pass."""
    result = _run_target(tmp_path, "echo '| f.py | COVERED |'\nexit 0\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "| f.py | COVERED |" in result.stdout


def test_the_target_passes_the_configured_flags_and_targets(tmp_path: Path) -> None:
    """The tool receives exactly the configured flags, then the targets."""
    _run_target(tmp_path, "exit 0\n")
    received = (tmp_path / "args.txt").read_text(encoding="utf-8").split()
    expected = (
        _variable("INTERROGATE_FLAGS").split()
        + _expanded("INTERROGATE_TARGETS").split()
    )
    assert received == expected


def test_the_target_fails_when_the_tool_fails(tmp_path: Path) -> None:
    """A failing tool fails the target."""
    result = _run_target(tmp_path, "echo 'FAILED'\nexit 1\n")
    assert result.returncode != 0, "a failing tool must fail the target"
    assert "FAILED" in result.stdout, "the failing report must still be printed"


@pytest.mark.parametrize(
    "cell",
    ["|  MISSED |", "|   MISSED    |"],
    ids=["interrogate-default-padding", "wider-padding"],
)
def test_the_target_fails_on_a_missed_row_the_percentage_hid(
    tmp_path: Path, cell: str
) -> None:
    """A MISSED row in a report from a successful run still fails the target.

    The guard must not depend on how many spaces pad the status cell.
    """
    result = _run_target(
        tmp_path,
        f"echo '| f.main (L3) {cell}'\necho 'RESULT: PASSED'\nexit 0\n",
    )
    assert result.returncode != 0, "a MISSED row must fail the target"
    assert "undocumented definitions remain" in result.stderr, result.stderr
    assert "f.main (L3)" in result.stdout, "the report must still be printed"
