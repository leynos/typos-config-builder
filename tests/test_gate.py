"""Contracts for the combined build, Typos, and phrase-correction gate."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import subprocess  # noqa: S404
import typing as typ

import pytest
from test_phrases import CORRECTION, PROHIBITED, build_repository, cache_text

from typos_config_builder import cli, gate

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Spliced so this file never contains a plain-British form that the
# repository's own gate would report.
PLAIN_BRITISH = "organ" + "ise"
OXFORD = "organ" + "ize"


@dc.dataclass(frozen=True, slots=True)
class RunnerCall:
    """Record one invocation of the injected Typos runner.

    Attributes
    ----------
    argv
        Complete command line the gate assembled.
    cwd
        Working directory the gate selected.
    has_config
        Whether generated configuration existed when the call was made.
    """

    argv: tuple[str, ...]
    cwd: pathlib.Path
    has_config: bool


class FakeRunner:
    """Record Typos invocations and replay configured exit codes."""

    def __init__(self, *returncodes: int) -> None:
        """Queue exit codes, repeating the last once the queue is exhausted."""
        self._returncodes = returncodes or (0,)
        self.calls: list[RunnerCall] = []

    def __call__(
        self,
        args: cabc.Sequence[str],
        /,
        *,
        cwd: pathlib.Path,
        check: bool,
        stdin: int,
    ) -> subprocess.CompletedProcess[bytes]:
        """Record one invocation and return its configured completion."""
        assert not check, "the gate must inspect exit codes rather than raise"
        assert stdin == subprocess.DEVNULL, "Typos must not inherit standard input"
        index = min(len(self.calls), len(self._returncodes) - 1)
        self.calls.append(RunnerCall(tuple(args), cwd, (cwd / "typos.toml").is_file()))
        return subprocess.CompletedProcess(list(args), self._returncodes[index])


@pytest.fixture
def authority(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a local authority carrying the prohibited phrase and one stem."""
    path = tmp_path / "authority.toml"
    path.write_text(cache_text(), encoding="utf-8")
    return path


def fake_paths(count: int) -> tuple[pathlib.Path, ...]:
    """Return distinct repository-relative Markdown paths."""
    return tuple(pathlib.Path(f"doc-{index}.md") for index in range(count))


def test_select_files_keeps_markdown_case_insensitively() -> None:
    """Markdown scope keeps only Markdown, whatever the suffix case."""
    tracked = (
        pathlib.Path("README.md"),
        pathlib.Path("docs/GUIDE.MD"),
        pathlib.Path("notes.txt"),
        pathlib.Path("Makefile"),
    )

    assert gate.select_files(tracked, "markdown") == (
        pathlib.Path("README.md"),
        pathlib.Path("docs/GUIDE.MD"),
    )


def test_scope_all_includes_non_markdown() -> None:
    """The ``all`` scope submits every tracked path unchanged."""
    tracked = (pathlib.Path("README.md"), pathlib.Path("notes.txt"))

    assert gate.select_files(tracked, "all") == tracked


def test_run_typos_chunks_long_file_lists(tmp_path: pathlib.Path) -> None:
    """A long file list is split so no command line grows without bound."""
    runner = FakeRunner(0)

    exit_code = gate.run_typos(tmp_path, fake_paths(1200), hidden=False, runner=runner)

    assert exit_code == 0
    assert len(runner.calls) == 3
    lengths = [
        len([argument for argument in call.argv if argument.startswith("doc-")])
        for call in runner.calls
    ]
    assert lengths == [500, 500, 200]


def test_run_typos_skips_invocation_without_files(tmp_path: pathlib.Path) -> None:
    """An empty selection succeeds without starting Typos at all."""
    runner = FakeRunner(2)

    exit_code = gate.run_typos(tmp_path, (), hidden=False, runner=runner)

    assert exit_code == 0
    assert not runner.calls, "Typos ran with nothing to check"


def test_run_typos_returns_the_worst_exit_code(tmp_path: pathlib.Path) -> None:
    """One failing chunk fails the whole Typos stage."""
    runner = FakeRunner(0, 2, 0)

    exit_code = gate.run_typos(tmp_path, fake_paths(1200), hidden=False, runner=runner)

    assert exit_code == 2


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


def test_scope_all_adds_the_hidden_flag(tmp_path: pathlib.Path) -> None:
    """Checking every tracked path includes dotted directories."""
    runner = FakeRunner(0)

    gate.run_typos(tmp_path, fake_paths(1), hidden=True, runner=runner)

    assert "--hidden" in runner.calls[0].argv


def test_gate_builds_configuration_before_running_typos(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """Generated configuration exists by the time Typos is invoked."""
    repository = build_repository(tmp_path, {"README.md": "Ordinary prose only.\n"})
    runner = FakeRunner(0)

    result = gate.gate(
        repository, gate.GateOptions(source=str(authority)), runner=runner
    )

    assert runner.calls[0].has_config, "Typos ran before typos.toml was written"
    assert result.build.output == repository / "typos.toml"
    assert result.is_clean
    assert result.status == 0


def test_gate_checks_phrases_after_typos_reports_findings(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """A Typos finding never short-circuits the phrase stage."""
    repository = build_repository(tmp_path, {"README.md": f"Prefer {PROHIBITED}.\n"})
    runner = FakeRunner(2)

    result = gate.gate(
        repository, gate.GateOptions(source=str(authority)), runner=runner
    )

    assert result.typos_exit == 2
    assert [finding.correction for finding in result.phrase_findings] == [CORRECTION]
    assert result.status == 2
    assert not result.is_clean


def test_gate_markdown_scope_excludes_other_files(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """The default scope submits tracked Markdown only."""
    repository = build_repository(
        tmp_path,
        {"README.md": "Ordinary prose only.\n", "notes.txt": "Plain notes.\n"},
    )
    runner = FakeRunner(0)

    gate.gate(repository, gate.GateOptions(source=str(authority)), runner=runner)

    argv = runner.calls[0].argv
    assert "README.md" in argv
    assert "notes.txt" not in argv


def test_gate_scope_all_submits_every_tracked_path(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """The ``all`` scope submits non-Markdown paths and searches hidden files."""
    repository = build_repository(
        tmp_path,
        {"README.md": "Ordinary prose only.\n", "notes.txt": "Plain notes.\n"},
    )
    runner = FakeRunner(0)

    gate.gate(
        repository,
        gate.GateOptions(source=str(authority), scope="all"),
        runner=runner,
    )

    argv = runner.calls[0].argv
    assert "notes.txt" in argv
    assert "--hidden" in argv


def test_missing_typos_binary_exits_one_through_the_cli(
    tmp_path: pathlib.Path,
    authority: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An absent Typos binary is a concise error rather than a traceback."""
    repository = build_repository(tmp_path, {"README.md": "Ordinary prose only.\n"})

    def unavailable() -> pathlib.Path:
        """Stand in for an environment without the pinned Typos binary."""
        message = "typos was not found"
        raise gate.TyposUnavailableError(message)

    monkeypatch.setattr(gate, "typos_executable", unavailable)

    with pytest.raises(SystemExit) as exit_status:
        cli.app([
            "gate",
            "--repository",
            str(repository),
            "--source",
            str(authority),
        ])

    captured = capsys.readouterr()
    assert exit_status.value.code == 1
    assert captured.err.strip() == "error: typos was not found"
    assert "Traceback" not in captured.err


@pytest.mark.slow
def test_real_typos_reports_a_plain_british_spelling(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """The pinned binary rejects a plain-British form of an Oxford stem."""
    repository = build_repository(
        tmp_path, {"README.md": f"We {PLAIN_BRITISH} the estate.\n"}
    )

    result = gate.gate(repository, gate.GateOptions(source=str(authority)))

    assert result.typos_exit == 2
    assert result.status == 2


@pytest.mark.slow
def test_real_typos_accepts_oxford_spelling(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """A clean repository passes every stage of the gate."""
    repository = build_repository(tmp_path, {"README.md": f"We {OXFORD} the estate.\n"})

    result = gate.gate(repository, gate.GateOptions(source=str(authority)))

    assert result.typos_exit == 0
    assert result.is_clean
