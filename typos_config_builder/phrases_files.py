"""Select and read the tracked files the phrase scanner may examine.

Deciding which tracked paths carry scannable text is a separate concern from
matching phrases within that text: it deals with Git enumeration, policy
documents, symlinks, submodule gitlinks, and the worktree boundary. Keeping it
here leaves :mod:`typos_config_builder.phrases` to the matching itself.

Content that is not valid UTF-8 is binary as far as a phrase check is
concerned, so it is skipped with a bounded diagnostic. Selection still fails
closed on a read error, which means the worktree changed under the scan or an
entry cannot be opened, because a silent skip there hides exactly the files
most likely to have drifted.

Diagnostics expose only bounded decisions; tracked paths and file contents are
deliberately excluded from logs.
"""

from __future__ import annotations

import logging
import os
import pathlib
import shutil
import stat

# The phrase gate enumerates tracked files through Git by design; the
# command is resolved from PATH and its arguments are never user text.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import typing as typ

from typos_config_builder import builder

if typ.TYPE_CHECKING:
    import collections.abc as cabc

LOGGER = logging.getLogger(__name__)

#: Index mode Git records for a submodule gitlink. A gitlink names a commit in
#: another repository rather than tracked text, so no stage may submit it.
GITLINK_MODE = "160000"

#: Policy documents describe prohibited phrases and must never be scanned for
#: them, otherwise the policy itself becomes a finding.
POLICY_PATHS = frozenset({
    pathlib.Path(builder.CACHE_NAME),
    pathlib.Path(builder.METADATA_NAME),
    pathlib.Path(builder.OVERLAY_NAME),
    pathlib.Path(builder.OUTPUT_NAME),
})

# One MiB bounds the memory and time spent on a tracked-file read; larger files
# are skipped because they are not useful prose inputs for phrase checking.
MAX_FILE_SIZE_BYTES = 1024 * 1024


class PhraseScanError(Exception):
    """Report that a tracked file could not be read.

    Attributes
    ----------
    path
        Repository-relative path that could not be scanned.
    """

    def __init__(self, path: pathlib.Path) -> None:
        """Record the tracked path that could not be scanned.

        Parameters
        ----------
        path
            Repository-relative path that could not be read.
        """
        self.path = path
        super().__init__(f"tracked file could not be scanned: {path}")


def _paths_outside_submodules(listing: str) -> set[pathlib.Path]:
    """Parse ``git ls-files -z --stage`` output, dropping submodule gitlinks."""
    # Each record is "mode sha stage\tpath", NUL-terminated, so a path
    # containing whitespace or a tab survives the split intact. An unmerged
    # path appears once per stage, hence the set.
    paths: set[pathlib.Path] = set()
    for record in filter(None, listing.split("\0")):
        attributes, _, relative = record.partition("\t")
        if attributes.partition(" ")[0] == GITLINK_MODE:
            continue
        paths.add(pathlib.Path(relative))
    return paths


def tracked_files(repository: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Return a repository's Git-tracked paths in deterministic order.

    Submodule gitlinks are omitted. Git records them as commits in another
    repository rather than as tracked text, so neither the phrase scanner nor
    Typos has anything to read at such a path.

    Parameters
    ----------
    repository
        Git worktree to enumerate.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Sorted repository-relative tracked paths, without gitlinks.

    Raises
    ------
    FileNotFoundError
        If ``git`` is not available on the executable search path.
    subprocess.CalledProcessError
        If ``git`` cannot enumerate the repository's tracked files.

    Examples
    --------
    >>> tracked_files(pathlib.Path("."))  # doctest: +SKIP
    (PosixPath('README.md'),)
    """
    executable = shutil.which("git")
    if executable is None:
        message = "git was not found on PATH; it is required to list tracked files"
        raise FileNotFoundError(message)
    listing = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        # --stage carries the index mode, which plain ls-files discards, and
        # the mode is the only reliable way to recognize a gitlink.
        [executable, "-C", str(repository), "ls-files", "-z", "--stage"],
        check=True,
        capture_output=True,
        # Git does not read standard input here, but a command double standing
        # in for it might. A shim waiting on an inherited terminal would wedge
        # the gate rather than fail it.
        stdin=subprocess.DEVNULL,
        text=True,
    ).stdout
    return tuple(sorted(_paths_outside_submodules(listing)))


def is_inside_worktree(candidate: pathlib.Path, root: pathlib.Path) -> bool:
    """Report whether a tracked path stays within the worktree boundary.

    Symlinks are never followed. A path whose parent is a symlink is not
    itself one, yet still resolves outside the worktree, so the resolved path
    is checked as well.

    Parameters
    ----------
    candidate
        Absolute path to the tracked entry on disk.
    root
        Resolved worktree the path must remain within.

    Returns
    -------
    bool
        True when the path names an entry inside the worktree.

    Examples
    --------
    >>> root = pathlib.Path(".").resolve()
    >>> is_inside_worktree(root / "README.md", root)  # doctest: +SKIP
    True
    """
    if candidate.is_symlink():
        return False
    return candidate.resolve().is_relative_to(root)


def is_scannable(
    candidate: pathlib.Path,
    root: pathlib.Path,
    *,
    max_file_size_bytes: int | None = None,
) -> bool:
    """Report whether a tracked path is a file lying inside the worktree.

    Symlinks and non-regular entries are skipped before either spelling
    checker opens the path. A size bound can be supplied by the phrase
    scanner; Typos selection does not apply that bound.

    Parameters
    ----------
    candidate
        Absolute path to the tracked file on disk.
    root
        Resolved worktree the path must remain within.
    max_file_size_bytes
        Optional maximum size for a phrase-scan input. When absent, regular
        files are not excluded by size.

    Returns
    -------
    bool
        True when the path is a regular file inside the worktree and within
        the optional size bound.

    Raises
    ------
    IsADirectoryError
        If a tracked file was replaced by a directory.
    OSError
        If metadata for the tracked path cannot be read.

    Examples
    --------
    >>> root = pathlib.Path(".").resolve()
    >>> is_scannable(root / "README.md", root)  # doctest: +SKIP
    True
    """
    if not is_inside_worktree(candidate, root):
        return False
    file_stat = os.lstat(candidate)
    if stat.S_ISDIR(file_stat.st_mode):
        raise IsADirectoryError(candidate)
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        return False
    return max_file_size_bytes is None or file_stat.st_size <= max_file_size_bytes


def select_scannable(
    repository: pathlib.Path, relatives: cabc.Sequence[pathlib.Path]
) -> tuple[pathlib.Path, ...]:
    """Keep the tracked paths an external checker may safely be pointed at.

    Typos follows a path given explicitly, symlink or not, so a tracked
    symlink leaving the worktree would take the check outside the repository.

    Parameters
    ----------
    repository
        Git worktree the paths are relative to.
    relatives
        Repository-relative tracked paths, in the order to preserve.

    Returns
    -------
    tuple[pathlib.Path, ...]
        The subset naming files inside the worktree, order preserved.

    Examples
    --------
    >>> select_scannable(pathlib.Path("."), ())
    ()
    """
    root = repository.resolve()
    selected = []
    for relative in relatives:
        try:
            if is_scannable(repository / relative, root):
                selected.append(relative)
        except IsADirectoryError:
            continue
    return tuple(selected)


def read_tracked_text(path: pathlib.Path, relative: pathlib.Path) -> str | None:
    r"""Read tracked UTF-8 text, reporting binary content as absent.

    Repositories track images, fonts, archives, and compiled artefacts. Such
    content carries no prose for a phrase check to examine, so it is skipped
    rather than failing the scan. Oversized files are also skipped, and the
    read is capped at one byte beyond the limit in case a file grows after its
    metadata was checked. A read error is a different matter: the worktree has
    changed under the scan, which must fail closed.

    Parameters
    ----------
    path
        Absolute path to read.
    relative
        Repository-relative path reported by a failure.

    Returns
    -------
    str | None
        Decoded bounded contents, or None when the file is oversized or not
        UTF-8.

    Raises
    ------
    PhraseScanError
        If the file cannot be read.

    Examples
    --------
    >>> read_tracked_text(  # doctest: +SKIP
    ...     pathlib.Path("/repo/README.md"), pathlib.Path("README.md")
    ... )
    '# Project\n'
    >>> read_tracked_text(  # doctest: +SKIP
    ...     pathlib.Path("/repo/logo.png"), pathlib.Path("logo.png")
    ... ) is None
    True
    """
    try:
        with path.open("rb") as source:
            content = source.read(MAX_FILE_SIZE_BYTES + 1)
        if len(content) > MAX_FILE_SIZE_BYTES:
            LOGGER.debug(
                "Phrase scan skipped a tracked file",
                extra={
                    "operation": "phrase-scan",
                    "decision": "skipped-oversized",
                    "error_class": "size-limit",
                },
            )
            return None
        return content.decode("utf-8")
    except UnicodeDecodeError:
        LOGGER.debug(
            "Phrase scan skipped a tracked file",
            extra={
                "operation": "phrase-scan",
                "decision": "skipped-binary",
                "error_class": "not-utf-8",
            },
        )
        return None
    except OSError as error:
        raise PhraseScanError(relative) from error
