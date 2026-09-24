"""Contract that each push and pull request runs the test suite once.

A pull request runs the suite under coverage in ``ci.yml``, and a push to
``main`` runs it under coverage in ``coverage-main.yml``. An
``act-validation.yml`` workflow used to run ``make test WITH_ACT=1`` on both
events as well. No test reads the ``RUN_ACT_VALIDATION`` variable that flag
sets, so a collection selects the same 251 tests either way, and the
workflow ran the whole suite a second time. It was removed. These tests keep
a second suite run from returning.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.workflow_reading import all_steps, load_document, triggers

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
COVERAGE_ACTION = "leynos/shared-actions/.github/actions/generate-coverage@"
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
SUITE_TARGETS = frozenset({"test", "all"})
SEPARATORS = re.compile(r"&&|\|\||[;|\n]")
PYTEST = re.compile(r"\bpytest\b")


def _make_targets(words: list[str]) -> list[str]:
    """Return the targets of a ``make`` call: its words less options and values."""
    start = next(
        (i for i, word in enumerate(words) if word == "make" or word.endswith("/make")),
        None,
    )
    if start is None:
        return []
    found: list[str] = []
    skip = False
    for word in words[start + 1 :]:
        if skip:
            skip = False
        elif word in MAKE_VALUE_OPTIONS:
            skip = True
        elif not word.startswith("-") and "=" not in word:
            found.append(word)
    return found


def runs_suite(command: str) -> bool:
    """Report whether a shell command runs the suite, in any spelling.

    Examples
    --------
    >>> runs_suite("make -C . test")
    True
    >>> runs_suite("make test-workflow-contracts")
    False
    """
    return bool(PYTEST.search(command)) or any(
        SUITE_TARGETS & set(_make_targets(segment.split()))
        for segment in SEPARATORS.split(command)
    )


def _document(name: str) -> dict[object, object]:
    """Parse one repository workflow."""
    return load_document((WORKFLOWS / name).read_text(encoding="utf-8"))


def _coverage_steps(name: str) -> list[dict[object, object]]:
    """Return the coverage generation steps of one workflow."""
    return [
        step
        for step in all_steps(_document(name))
        if str(step.get("uses", "")).startswith(COVERAGE_ACTION)
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("make test", True),
        ("make test WITH_ACT=1", True),
        ("make -j2 test", True),
        ("make -C . test", True),
        ("make all", True),
        ("set -eu && make test", True),
        ("uv run pytest -v", True),
        ("make test-workflow-contracts", False),
        ("make typecheck", False),
    ],
)
def test_the_suite_pattern(command: str, *, expected: bool) -> None:
    """Recognize every spelling of a suite run, and nothing longer."""
    assert runs_suite(command) is expected, command


def test_no_workflow_step_runs_the_suite() -> None:
    """Refuse a plain suite run anywhere; the coverage action is the one run."""
    repeated = [
        (path.name, step.get("run"))
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
        for step in all_steps(_document(path.name))
        if runs_suite(str(step.get("run", "")))
    ]
    assert not repeated, f"the suite runs outside coverage in {repeated!r}"


def test_a_pull_request_runs_coverage_in_ci() -> None:
    """Require `ci.yml`'s coverage step on pull requests."""
    assert "pull_request" in triggers(_document("ci.yml"))
    steps = _coverage_steps("ci.yml")
    assert len(steps) == 1, "expected one coverage step in ci.yml"
    assert steps[0].get("if") == "${{ github.event_name == 'pull_request' }}"


def test_a_push_to_main_runs_coverage_in_the_publisher() -> None:
    """Require an unguarded coverage step in `coverage-main.yml` on push."""
    push = triggers(_document("coverage-main.yml")).get("push")
    assert isinstance(push, dict), "coverage-main.yml must trigger on push"
    assert "main" in str(push.get("branches")), "the push trigger must name main"
    steps = _coverage_steps("coverage-main.yml")
    assert len(steps) == 1, "expected one coverage step in coverage-main.yml"
    assert "if" not in steps[0], "the publisher's coverage step must always run"
