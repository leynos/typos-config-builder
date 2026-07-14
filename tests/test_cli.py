"""Command-line contracts for the focused Cyclopts application."""

from __future__ import annotations

import typing as typ
from unittest import mock

import pytest

from typos_config_builder import cli
from typos_config_builder.cache import NetworkUnavailableError

app = cli.app

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


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("authority is missing"),
        NetworkUnavailableError("authority is unavailable"),
        ValueError("authority is invalid"),
    ],
)
def test_cli_translates_expected_builder_failures(
    failure: OSError | ValueError,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expected builder failures become concise command errors."""
    monkeypatch.setattr(cli, "build", mock.Mock(side_effect=failure))

    with pytest.raises(SystemExit) as error:
        app(["--repository", str(repository)])

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.err.strip() == f"error: {failure}"
    assert "Traceback" not in captured.err
