"""Contracts for the pinned Typos invocation the gate assembles.

The gate submits tracked paths to one binary in bounded command lines. These
contracts cover which binary is accepted, how paths are grouped, and how a
failed or signalled run is reported.
"""

from __future__ import annotations

import os
import pathlib
import sys
import typing as typ

import pytest
from conftest import FakeRunner

from typos_config_builder import gate

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from conftest import RunnerCall

#: Width chosen so nine such paths fit one command line and ten do not, which
#: keeps the chunking contracts independent of the interpreter's own path.
WIDE_PATH_DIGITS = 3100


def fake_paths(count: int) -> tuple[pathlib.Path, ...]:
    """Return distinct repository-relative Markdown paths."""
    return tuple(pathlib.Path(f"doc-{index}.md") for index in range(count))


def wide_paths(count: int) -> tuple[pathlib.Path, ...]:
    """Return distinct Markdown paths wide enough to force several chunks."""
    return tuple(
        pathlib.Path(f"{index:0{WIDE_PATH_DIGITS}d}.md") for index in range(count)
    )


def command_bytes(argv: cabc.Sequence[str]) -> int:
    """Return the byte cost of one assembled command line."""
    return sum(len(argument.encode()) + 1 for argument in argv)


def submitted_paths(calls: cabc.Sequence[RunnerCall]) -> list[str]:
    """Return every path submitted across the recorded invocations, in order."""
    return [
        argument for call in calls for argument in call.argv if argument.endswith(".md")
    ]


def test_run_typos_keeps_every_command_within_the_byte_budget(
    tmp_path: pathlib.Path,
) -> None:
    """Chunking bounds the command line's bytes, not its number of paths."""
    paths = fake_paths(1200)
    runner = FakeRunner(0)

    exit_code = gate.run_typos(tmp_path, paths, hidden=False, runner=runner)

    assert exit_code == 0
    assert submitted_paths(runner.calls) == [str(path) for path in paths]
    for call in runner.calls:
        assert command_bytes(call.argv) <= gate.COMMAND_BUDGET_BYTES
    # Short paths cost far less than the budget, so they travel in one command.
    assert len(runner.calls) == 1


def test_run_typos_splits_wide_paths_into_full_chunks(
    tmp_path: pathlib.Path,
) -> None:
    """Wide paths split into chunks that each fill the budget before the next."""
    paths = wide_paths(27)
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, paths, hidden=False, runner=runner)

    assert submitted_paths(runner.calls) == [str(path) for path in paths]
    assert len(runner.calls) == 3
    for call, following in zip(runner.calls, runner.calls[1:], strict=False):
        first_following = command_bytes(submitted_paths([following])[:1])
        assert command_bytes(call.argv) <= gate.COMMAND_BUDGET_BYTES
        assert command_bytes(call.argv) + first_following > gate.COMMAND_BUDGET_BYTES


def test_run_typos_submits_an_oversized_path_alone(tmp_path: pathlib.Path) -> None:
    """A path larger than the whole budget is still checked, on its own."""
    oversized = pathlib.Path("x" * (gate.COMMAND_BUDGET_BYTES + 10) + ".md")
    paths = (pathlib.Path("first.md"), oversized, pathlib.Path("last.md"))
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, paths, hidden=False, runner=runner)

    assert submitted_paths(runner.calls) == [str(path) for path in paths]
    assert [len(submitted_paths([call])) for call in runner.calls] == [1, 1, 1]


def test_run_typos_skips_invocation_without_files(tmp_path: pathlib.Path) -> None:
    """An empty selection succeeds without starting Typos at all."""
    runner = FakeRunner(2)

    exit_code = gate.run_typos(tmp_path, (), hidden=False, runner=runner)

    assert exit_code == 0
    assert not runner.calls, "Typos ran with nothing to check"


def test_run_typos_returns_the_worst_exit_code(tmp_path: pathlib.Path) -> None:
    """One failing chunk fails the whole Typos stage."""
    runner = FakeRunner(0, 2, 0)

    exit_code = gate.run_typos(tmp_path, wide_paths(27), hidden=False, runner=runner)

    assert exit_code == 2


def test_run_typos_rejects_a_signalled_exit(tmp_path: pathlib.Path) -> None:
    """A Typos process killed by a signal fails the stage rather than passing."""
    runner = FakeRunner(-9)

    with pytest.raises(gate.TyposFailedError) as error:
        gate.run_typos(tmp_path, fake_paths(1), hidden=False, runner=runner)

    assert "signal 9" in str(error.value)


def test_run_typos_aggregates_a_failing_final_chunk(tmp_path: pathlib.Path) -> None:
    """A finding in the last chunk still fails the whole Typos stage."""
    runner = FakeRunner(0, 0, 2)

    exit_code = gate.run_typos(tmp_path, wide_paths(27), hidden=False, runner=runner)

    assert exit_code == 2
    assert len(runner.calls) == 3


def test_run_typos_passes_the_generated_configuration(
    tmp_path: pathlib.Path,
) -> None:
    """Typos is pointed at the generated configuration and honours exclusions."""
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, fake_paths(1), hidden=False, runner=runner)

    argv = runner.calls[0].argv
    assert argv[0].endswith(("typos", "typos.exe"))
    assert argv[1:4] == ("--config", "typos.toml", "--force-exclude")
    assert "--hidden" not in argv
    assert runner.calls[0].cwd == tmp_path


def test_run_typos_separates_options_from_tracked_paths(
    tmp_path: pathlib.Path,
) -> None:
    """A tracked path beginning with a dash is passed as a path, not an option."""
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, (pathlib.Path("-hidden.md"),), hidden=True, runner=runner)

    argv = runner.calls[0].argv
    assert argv[-2:] == ("--", "-hidden.md")


def test_typos_executable_ignores_an_unrelated_binary_on_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the Typos installed beside the interpreter satisfies the gate."""
    environment = tmp_path / "environment" / "bin"
    environment.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    impostor = elsewhere / ("typos.exe" if os.name == "nt" else "typos")
    impostor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    impostor.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(environment / "python"))
    monkeypatch.setenv("PATH", str(elsewhere))

    with pytest.raises(gate.TyposUnavailableError):
        gate.typos_executable()


def test_scope_all_adds_the_hidden_flag(tmp_path: pathlib.Path) -> None:
    """Checking every tracked path includes dotted directories."""
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, fake_paths(1), hidden=True, runner=runner)

    assert "--hidden" in runner.calls[0].argv
