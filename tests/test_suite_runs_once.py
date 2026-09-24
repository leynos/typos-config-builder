"""Contract that each push and pull request runs the test suite once.

A pull request runs the suite under coverage in ``ci.yml``, and a push to
``main`` runs it under coverage in ``coverage-main.yml``. An
``act-validation.yml`` workflow used to run ``make test WITH_ACT=1`` on both
events as well. No test reads the ``RUN_ACT_VALIDATION`` variable that flag
sets, so a collection selects the same 251 tests either way, and the
workflow ran the whole suite a second time. It was removed. These tests keep
a second suite run from returning, across every workflow and every local
composite action, since a step there runs as part of whatever uses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.suite_commands import runs_suite
from tests.workflow_reading import all_steps, filter_names, load_document, triggers

GITHUB = Path(__file__).resolve().parents[1] / ".github"
WORKFLOWS = GITHUB / "workflows"
COVERAGE_ACTION = "leynos/shared-actions/.github/actions/generate-coverage@"
PULL_REQUEST_GUARD = "${{ github.event_name == 'pull_request' }}"


def _local_documents() -> dict[str, dict[object, object]]:
    """Parse every workflow and local composite action, by relative path."""
    paths = [*WORKFLOWS.glob("*.y*ml"), *GITHUB.glob("actions/**/action.y*ml")]
    return {
        str(path.relative_to(GITHUB)): load_document(path.read_text(encoding="utf-8"))
        for path in sorted(paths)
    }


def _document(name: str) -> dict[object, object]:
    """Parse one repository workflow."""
    return load_document((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("make test", True),
        ("make test WITH_ACT=1", True),
        ("make -j2 test", True),
        ("make -C . test", True),
        ("make all", True),
        ("make", True),
        ('make "test"', True),
        ("make lint&&make test", True),
        ("set -eu && make test", True),
        ("uv run pytest -v", True),
        ("uv run --with 'pytest>=8' python -m pytest -q", True),
        ("make test-workflow-contracts", False),
        ("make typecheck", False),
        ("echo pytest", False),
    ],
)
def test_the_suite_pattern(command: str, *, expected: bool) -> None:
    """Recognize every spelling of a suite run, and nothing longer."""
    assert runs_suite(command) is expected, command


def test_no_local_step_runs_the_suite() -> None:
    """Refuse a plain suite run in any workflow or local composite action."""
    repeated = [
        (where, step.get("run"))
        for where, document in _local_documents().items()
        for step in all_steps(document)
        if runs_suite(str(step.get("run", "")))
    ]
    assert not repeated, f"the suite runs outside coverage in {repeated!r}"


def test_coverage_runs_only_in_the_two_lanes() -> None:
    """Require the coverage action in ``ci.yml`` and the publisher alone."""
    lanes = {
        where: [
            step
            for step in all_steps(document)
            if str(step.get("uses", "")).startswith(COVERAGE_ACTION)
        ]
        for where, document in _local_documents().items()
    }
    placed = {where: len(steps) for where, steps in lanes.items() if steps}
    assert placed == {"workflows/ci.yml": 1, "workflows/coverage-main.yml": 1}, placed
    assert lanes["workflows/ci.yml"][0].get("if") == PULL_REQUEST_GUARD
    assert "if" not in lanes["workflows/coverage-main.yml"][0], (
        "the publisher's coverage step must always run"
    )


def test_every_pull_request_reaches_coverage() -> None:
    """Require an unfiltered ``pull_request`` trigger on ``ci.yml``."""
    pull_request = triggers(_document("ci.yml")).get("pull_request")
    assert pull_request in ("", None, {}), (
        f"ci.yml's pull_request trigger is filtered: {pull_request!r}"
    )


def test_every_push_to_main_reaches_the_publisher() -> None:
    """Require a push trigger naming ``main`` exactly on the publisher."""
    push = triggers(_document("coverage-main.yml")).get("push")
    assert isinstance(push, dict), "coverage-main.yml must trigger on push"
    assert "main" in filter_names(push.get("branches")), "the push must name main"
