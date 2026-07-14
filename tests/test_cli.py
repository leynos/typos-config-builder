"""Command-line contracts for the focused Cyclopts application."""

from __future__ import annotations

import typing as typ

import pytest

from typos_config_builder.cli import app

if typ.TYPE_CHECKING:
    from pathlib import Path

    from conftest import AuthorityFactory


def test_cli_generates_configuration(
    authority_factory: AuthorityFactory,
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default command generates configuration for an explicit repository."""
    with pytest.raises(SystemExit) as exit_status:
        app([
            "--repository",
            str(repository),
            "--source",
            str(authority_factory()),
        ])

    captured = capsys.readouterr()
    assert exit_status.value.code == 0
    assert not captured.err
    assert (repository / "typos.toml").is_file()


def test_cli_check_reports_actionable_drift(
    authority_factory: AuthorityFactory,
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check mode identifies the generated path and the required remedy."""
    with pytest.raises(SystemExit) as error:
        app([
            "--repository",
            str(repository),
            "--source",
            str(authority_factory()),
            "--check",
        ])

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert "typos.toml" in captured.err
    assert "drift" in captured.err.lower()
