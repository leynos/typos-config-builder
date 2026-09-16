"""Contracts for tracked-file selection in the phrase gate.

These tests cover how the phrase check chooses which tracked files to read:
symlinks and submodule gitlinks are skipped, paths that resolve outside the
repository are skipped, and unreadable or vanished tracked files fail closed.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess  # noqa: S404 - fixture Git commands with fixed arguments.

import pytest
from conftest import (
    CORRECTION,
    PROHIBITED,
    build_repository,
    cache_text,
    overlay_text,
)

from typos_config_builder import phrases

# Placeholder commit identifier for a submodule gitlink. Git records a
# gitlink without resolving the object, so the commit need not exist.
GITLINK_COMMIT = "0" * 39 + "1"


def _git(repository: pathlib.Path, *arguments: str) -> None:
    """Run one Git command inside a fixture repository."""
    subprocess.run(  # noqa: S603 - fixed, fixture-controlled arguments.
        ["git", "-C", str(repository), *arguments],  # noqa: S607 - git on PATH.
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def test_tracked_symlinks_are_skipped(tmp_path: pathlib.Path) -> None:
    """A tracked symlink is never followed out of the repository."""
    repository = build_repository(tmp_path, {".typos-oxendict-base.toml": cache_text()})
    external = tmp_path / "external.txt"
    external.write_text(f"{PROHIBITED}\n", encoding="utf-8")
    (repository / "external.txt").symlink_to(external)
    _git(repository, "add", "external.txt")

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the scan followed a tracked symlink out of the repository"
    )


def test_tracked_path_behind_a_symlinked_parent_is_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """A symlinked parent directory cannot smuggle outside text into the scan."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "docs/guide.md": "Ordinary prose only.\n",
        },
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "guide.md").write_text(f"{PROHIBITED}\n", encoding="utf-8")
    shutil.rmtree(repository / "docs")
    (repository / "docs").symlink_to(outside, target_is_directory=True)

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the scan read a file outside the repository through a symlinked parent"
    )


def test_policy_documents_are_not_scanned(tmp_path: pathlib.Path) -> None:
    """The cache, overlay, metadata, and generated config are skipped."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            ".typos-oxendict-base.json": f'{{"note": "{PROHIBITED}"}}\n',
            "typos.local.toml": overlay_text(corrections=((PROHIBITED, CORRECTION),)),
            "typos.toml": f"# Policy for {PROHIBITED} corrections.\n",
        },
    )

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "a policy document was reported as a finding"
    )


def test_tracked_file_removed_after_enumeration_fails_closed(
    tmp_path: pathlib.Path,
) -> None:
    """A vanished tracked file raises rather than silently passing."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    (repository / "README.md").unlink()

    with pytest.raises(phrases.PhraseScanError) as error:
        phrases.find_phrases(repository, phrases.load_policy(repository))

    assert error.value.path == pathlib.Path("README.md")
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_tracked_submodule_directory_is_skipped(tmp_path: pathlib.Path) -> None:
    """A tracked submodule gitlink is skipped instead of failing the scan.

    The gitlink is recorded with ``git update-index --cacheinfo`` and an empty
    directory is created in its place. That route avoids committing an inner
    repository and relaxing Git's file-protocol policy, while reproducing what
    ``git ls-files`` reports for a checked-out submodule.
    """
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Ordinary prose.\n"},
    )
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{GITLINK_COMMIT},sub",
    )
    (repository / "sub").mkdir()
    (repository / "sub" / "NOTES.md").write_text(
        f"Prefer {PROHIBITED}.\n", encoding="utf-8"
    )

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the submodule's untracked content was scanned"
    )


def test_undecodable_tracked_file_fails_closed(tmp_path: pathlib.Path) -> None:
    """Tracked bytes that are not UTF-8 raise rather than being skipped."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Initially UTF-8.\n"},
    )
    (repository / "README.md").write_bytes(b"\xff\xfe")

    with pytest.raises(phrases.PhraseScanError) as error:
        phrases.find_phrases(repository, phrases.load_policy(repository))

    assert error.value.path == pathlib.Path("README.md")
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


def test_tracked_files_requires_a_git_repository(tmp_path: pathlib.Path) -> None:
    """Enumeration outside a repository surfaces the Git failure."""
    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    with pytest.raises(subprocess.CalledProcessError):
        phrases.tracked_files(outside)


def test_tracked_files_omits_a_submodule_gitlink(tmp_path: pathlib.Path) -> None:
    """Enumeration drops a gitlink, so no stage can submit it as text."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Ordinary prose.\n"},
    )
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{GITLINK_COMMIT},sub",
    )
    (repository / "sub").mkdir()

    tracked = phrases.tracked_files(repository)

    assert pathlib.Path("README.md") in tracked
    assert pathlib.Path("sub") not in tracked, "a gitlink was enumerated as a file"


def test_tracked_file_replaced_by_a_directory_fails_closed(
    tmp_path: pathlib.Path,
) -> None:
    """A tracked file that became a directory raises rather than being skipped."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    (repository / "README.md").unlink()
    (repository / "README.md").mkdir()

    with pytest.raises(phrases.PhraseScanError) as error:
        phrases.find_phrases(repository, phrases.load_policy(repository))

    assert error.value.path == pathlib.Path("README.md")
