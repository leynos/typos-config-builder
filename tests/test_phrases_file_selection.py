"""Contracts for tracked paths passed to the external Typos checker."""

from __future__ import annotations

import pathlib
import typing as typ

from typos_config_builder import phrases_files

if typ.TYPE_CHECKING:
    import pytest


def test_external_checker_selection_skips_a_directory(
    tmp_path: pathlib.Path,
) -> None:
    """Directories are omitted from the paths sent to the external checker."""
    (tmp_path / "docs").mkdir()
    selected = phrases_files.select_scannable(tmp_path, (pathlib.Path("docs"),))
    assert not selected, "skip directories"


def test_external_checker_selection_skips_a_vanished_path(
    tmp_path: pathlib.Path,
) -> None:
    """Typos selection tolerates a tracked path removed after Git listing."""
    selected = phrases_files.select_scannable(tmp_path, (pathlib.Path("vanished.md"),))
    assert not selected, "skip paths that vanished after Git enumeration"


def test_external_checker_selection_skips_a_regular_file_parent(
    tmp_path: pathlib.Path,
) -> None:
    """A replaced parent cannot leave a stale tracked path for Typos."""
    (tmp_path / "docs").write_text("replaced directory", encoding="utf-8")
    tracked_path = pathlib.Path("docs/README.md")

    selected = phrases_files.select_scannable(tmp_path, (tracked_path,))

    assert not selected, "skip tracked paths beneath a regular-file parent"


def test_large_regular_file_stays_in_typos_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typos selection is not limited by the phrase scanner's input cap."""
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)
    (tmp_path / "README.md").write_text("Large enough.", encoding="utf-8")

    expected = (pathlib.Path("README.md"),)
    selected = phrases_files.select_scannable(tmp_path, expected)
    assert selected == expected, "keep large regular files in Typos selection"
