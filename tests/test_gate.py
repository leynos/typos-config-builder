"""Contracts for the gate's staging and its command-line boundary.

The Typos invocation itself is contracted in
:mod:`tests.test_gate_typos`; this module covers scope selection, the order
of the gate's stages, and how failures surface through the command line.
"""

from __future__ import annotations

import os
import pathlib
import subprocess  # noqa: S404 - only a fixture repository's own Git calls.

import pytest
from conftest import CORRECTION, PROHIBITED, FakeRunner, build_repository

from typos_config_builder import cli, gate

# Spliced so this file never contains a plain-British form that the
# repository's own gate would report.
PLAIN_BRITISH = "organ" + "ise"
OXFORD = "organ" + "ize"


def stage(repository: pathlib.Path, *paths: str) -> None:
    """Stage extra paths in a fixture repository after it was built."""
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), "add", *paths],  # noqa: S607
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


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


@pytest.mark.skipif(os.name == "nt", reason="the stub relies on POSIX signals")
def test_signalled_typos_exits_one_through_the_cli(
    tmp_path: pathlib.Path,
    authority: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Typos binary killed by a signal is an error, never a clean gate."""
    repository = build_repository(tmp_path, {"README.md": "Ordinary prose only.\n"})
    stub = tmp_path / "typos-stub"
    stub.write_text("#!/bin/sh\nkill -9 $$\n", encoding="utf-8")
    stub.chmod(0o755)

    def signalled() -> pathlib.Path:
        """Stand in for a Typos binary that is killed while running."""
        return stub

    monkeypatch.setattr(gate, "typos_executable", signalled)

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
    assert "signal 9" in captured.err
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


def test_gate_withholds_a_symlink_leaving_the_repository(
    tmp_path: pathlib.Path, authority: pathlib.Path
) -> None:
    """Typos never receives a tracked symlink whose target lies outside."""
    repository = build_repository(tmp_path, {"README.md": "Ordinary prose only.\n"})
    external = tmp_path / "external.md"
    external.write_text("Ordinary prose only.\n", encoding="utf-8")
    (repository / "external.md").symlink_to(external)
    stage(repository, "external.md")
    runner = FakeRunner(0)

    gate.gate(repository, gate.GateOptions(source=str(authority)), runner=runner)

    argv = runner.calls[0].argv
    assert "README.md" in argv
    assert "external.md" not in argv, "Typos was pointed outside the worktree"
