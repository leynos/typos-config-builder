"""Command-line contracts for the focused Cyclopts application."""

from __future__ import annotations

import typing as typ
from unittest import mock

import pytest
from conftest import CORRECTION, PROHIBITED, build_repository, cache_text

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


def test_check_phrases_reports_findings_and_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Findings are printed as locations and the command exits two."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": f"Prefer {PROHIBITED}.\n",
        },
    )

    with pytest.raises(SystemExit) as exit_status:
        app(["check-phrases", "--repository", str(repository)])

    captured = capsys.readouterr()
    assert exit_status.value.code == 2
    assert captured.out == f"README.md:1:8: {PROHIBITED} -> {CORRECTION}\n"
    assert not captured.err


def test_check_phrases_exits_zero_without_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clean repository produces no output and a successful exit."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": "Ordinary prose only.\n",
        },
    )

    with pytest.raises(SystemExit) as exit_status:
        app(["check-phrases", "--repository", str(repository)])

    captured = capsys.readouterr()
    assert exit_status.value.code == 0
    assert not captured.out
    assert not captured.err


def test_check_phrases_reports_a_missing_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repository without a cache exits one with a concise diagnostic."""
    repository = build_repository(tmp_path, {"README.md": "Nothing to see.\n"})

    with pytest.raises(SystemExit) as exit_status:
        app(["check-phrases", "--repository", str(repository)])

    captured = capsys.readouterr()
    assert exit_status.value.code == 1
    assert captured.err.startswith("error: ")
    assert "typos-config-builder" in captured.err
    assert "Traceback" not in captured.err


def _gate_authority(tmp_path: Path) -> Path:
    """Write a local authority carrying one stem and the prohibited phrase."""
    authority = tmp_path / "gate-authority.toml"
    authority.write_text(cache_text(), encoding="utf-8")
    return authority


def test_gate_exits_zero_for_a_clean_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole gate reports the refresh status and succeeds."""
    repository = build_repository(tmp_path, {"README.md": "Ordinary prose.\n"})

    with pytest.raises(SystemExit) as exit_status:
        app([
            "gate",
            "--repository",
            str(repository),
            "--source",
            str(_gate_authority(tmp_path)),
        ])

    captured = capsys.readouterr()
    assert exit_status.value.code == 0
    assert "typos.toml" in captured.out
    assert not captured.err


def test_gate_exits_two_for_a_prohibited_phrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A phrase finding fails the gate and is printed as a location."""
    repository = build_repository(tmp_path, {"README.md": f"Prefer {PROHIBITED}.\n"})

    with pytest.raises(SystemExit) as exit_status:
        app([
            "gate",
            "--repository",
            str(repository),
            "--source",
            str(_gate_authority(tmp_path)),
        ])

    captured = capsys.readouterr()
    assert exit_status.value.code == 2
    assert f"README.md:1:8: {PROHIBITED} -> {CORRECTION}" in captured.out
