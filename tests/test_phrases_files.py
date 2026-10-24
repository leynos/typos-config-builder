"""Contracts for tracked-file selection in the phrase gate.

These tests cover how the phrase check chooses which tracked files to read:
symlinks and submodule gitlinks are skipped, paths that resolve outside the
repository are skipped, content that is not UTF-8 is treated as binary and
skipped, and unreadable or vanished tracked files fail closed.
"""

from __future__ import annotations

import io
import logging
import os
import pathlib
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - fixture Git commands with fixed arguments.
import typing as typ

import pytest
from conftest import (
    CORRECTION,
    PROHIBITED,
    build_repository,
    cache_text,
    overlay_text,
)

from typos_config_builder import phrases, phrases_files

# Placeholder commit identifier for a submodule gitlink. Git records a
# gitlink without resolving the object, so the commit need not exist.
GITLINK_COMMIT = "0" * 39 + "1"

# A UTF-16 byte-order mark followed by a NUL: invalid UTF-8, and the kind of
# leading bytes a tracked image, font, or compiled artefact begins with.
BINARY_BYTES = b"\xff\xfe\x00binary"


def _git(repository: pathlib.Path, *arguments: str) -> None:
    """Run one Git command inside a fixture repository."""
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed, fixture-controlled arguments.
        ["git", "-C", str(repository), *arguments],  # ruff: ignore[start-process-with-partial-path] - git on PATH.
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


@pytest.mark.skipif(not pathlib.Path("/dev/zero").exists(), reason="requires /dev/zero")
def test_tracked_symlink_to_unbounded_device_is_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """A tracked link to a device must not make the phrase gate block."""
    repository = build_repository(tmp_path, {".typos-oxendict-base.toml": cache_text()})
    link = repository / "README.md"
    link.symlink_to("/dev/zero")
    _git(repository, "add", "README.md")

    assert not phrases.find_phrases(repository, phrases.load_policy(repository))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO support")
def test_fifo_is_not_scannable(tmp_path: pathlib.Path) -> None:
    """A FIFO is rejected before opening so a scan cannot wait for a writer."""
    repository = build_repository(tmp_path, {".typos-oxendict-base.toml": cache_text()})
    fifo = repository / "pipe.md"
    os.mkfifo(fifo)

    assert not phrases_files.is_scannable(fifo, repository.resolve())


@pytest.mark.skipif(not pathlib.Path("/dev/zero").exists(), reason="requires /dev/zero")
def test_device_is_not_scannable(tmp_path: pathlib.Path) -> None:
    """A device path is rejected before a scanner can read from it."""
    assert not phrases_files.is_scannable(pathlib.Path("/dev/zero"), tmp_path.resolve())


def test_external_checker_selection_skips_a_directory(tmp_path: pathlib.Path) -> None:
    """Directories are omitted from the paths sent to the external checker."""
    directory = tmp_path / "docs"
    directory.mkdir()

    assert not phrases_files.select_scannable(tmp_path, (pathlib.Path("docs"),))


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


def test_missing_external_target_behind_symlinked_parent_is_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """A missing external target is still outside the scan boundary."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "docs/guide.md": "Text.\n"},
    )
    shutil.rmtree(repository / "docs")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / "docs").symlink_to(outside, target_is_directory=True)

    assert not phrases.find_phrases(repository, phrases.load_policy(repository))


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


def test_undecodable_tracked_file_is_skipped_as_binary(
    tmp_path: pathlib.Path,
) -> None:
    """Tracked bytes that are not UTF-8 are binary content, not a scan failure."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Initially UTF-8.\n"},
    )
    (repository / "README.md").write_bytes(BINARY_BYTES)

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "binary tracked content produced a finding"
    )


def test_text_beside_a_binary_tracked_file_is_still_scanned(
    tmp_path: pathlib.Path,
) -> None:
    """Skipping is per file, so decodable siblings are still reported."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "NOTES.md": f"Prefer {PROHIBITED}.\n",
            "README.md": "Initially UTF-8.\n",
        },
    )
    (repository / "README.md").write_bytes(BINARY_BYTES)

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert [finding.path for finding in findings] == [pathlib.Path("NOTES.md")]


def test_oversized_tracked_text_is_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An oversized regular file cannot consume unbounded scan memory."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": f"{PROHIBITED} and additional text.\n",
        },
    )
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)

    assert not phrases.find_phrases(repository, phrases.load_policy(repository))


def test_large_regular_file_stays_in_typos_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typos selection is not limited by the phrase scanner's input cap."""
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)
    (tmp_path / "README.md").write_text("Large enough.", encoding="utf-8")

    assert phrases_files.select_scannable(tmp_path, (pathlib.Path("README.md"),)) == (
        pathlib.Path("README.md"),
    )


def test_bounded_read_requests_only_one_byte_past_the_limit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read itself stays bounded if a file grows after the size check."""
    monkeypatch.setattr(phrases_files, "MAX_FILE_SIZE_BYTES", 8)
    requested_sizes: list[int | None] = []

    class ReadProbe(io.BytesIO):
        """Record the maximum byte count requested from the reader."""

        def read(self, size: int | None = -1) -> bytes:
            """Capture the bound while returning more data than it permits."""
            requested_sizes.append(size)
            return super().read(size)

    reader = ReadProbe(b"x" * 9)
    monkeypatch.setattr(pathlib.Path, "open", lambda *_args, **_kwargs: reader)

    actual = phrases_files.read_tracked_text(
        tmp_path / "README.md", pathlib.Path("README.md")
    )

    assert actual is None
    assert requested_sizes == [9]


def test_read_failure_is_wrapped_with_the_tracked_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read error stays fail-closed and identifies the tracked path."""
    path = tmp_path / "README.md"
    relative = pathlib.Path("README.md")

    def fail_open(*_args: object, **_kwargs: object) -> typ.Never:
        """Simulate a worktree entry that cannot be opened."""
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "open", fail_open)

    with pytest.raises(phrases_files.PhraseScanError) as error:
        phrases_files.read_tracked_text(path, relative)

    assert error.value.path == relative
    assert isinstance(error.value.__cause__, PermissionError)


def test_ordinary_utf8_text_still_reports_phrase_findings(
    tmp_path: pathlib.Path,
) -> None:
    """CR-only UTF-8 prose retains the correct phrase finding line number."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": f"Introductory prose.\rPrefer {PROHIBITED}.\r",
        },
    )

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert findings == (
        phrases.PhraseFinding(pathlib.Path("README.md"), 2, 8, PROHIBITED, CORRECTION),
    )


def test_skipping_binary_content_logs_one_bounded_record(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The skip is visible as a bounded decision that carries no file content."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Initially UTF-8.\n"},
    )
    (repository / "README.md").write_bytes(BINARY_BYTES)

    with caplog.at_level(logging.DEBUG, logger="typos_config_builder"):
        phrases.find_phrases(repository, phrases.load_policy(repository))

    skips = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "phrase-scan"
    ]
    assert len(skips) == 1
    assert skips[0].levelno in {logging.DEBUG, logging.INFO}
    assert getattr(skips[0], "decision", None) == "skipped-binary"
    assert "README" not in skips[0].getMessage()


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
