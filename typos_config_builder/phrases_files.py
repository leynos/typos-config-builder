"""Select and read the tracked files the phrase scanner may examine.

Deciding which tracked paths carry scannable text is a separate concern from
matching phrases within that text: it deals with Git enumeration, policy
documents, symlinks, submodule gitlinks, and the worktree boundary. Keeping it
here leaves :mod:`typos_config_builder.phrases` to the matching itself.

Content that is not valid UTF-8 is binary as far as a phrase check is
concerned, so it is skipped. Selection still fails closed on a read error,
which means the worktree changed under the scan or an entry cannot be opened,
because a silent skip there hides exactly the files most likely to have
drifted. The phrase orchestration layer logs fixed, path-free outcomes returned
by these helpers.
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import stat

# The phrase gate enumerates tracked files through Git by design; the
# command is resolved from PATH and its arguments are never user text.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import typing as typ

from typos_config_builder import builder
from typos_config_builder.phrase_file_open import open_tracked_file
from typos_config_builder.phrase_scan_results import (
    ReadOutcome,
    ScanEligibility,
    TrackedTextRead,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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
    checker opens the path. Replacing a tracked file with a directory raises
    because it indicates worktree drift. A size bound can be supplied by the
    phrase scanner; Typos selection does not apply that bound.

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
    eligibility = scan_eligibility(
        candidate, root, max_file_size_bytes=max_file_size_bytes
    )
    if eligibility is ScanEligibility.DIRECTORY_REPLACEMENT:
        raise IsADirectoryError(candidate)
    return eligibility.is_eligible


def scan_eligibility(
    candidate: pathlib.Path,
    root: pathlib.Path,
    *,
    max_file_size_bytes: int | None = None,
) -> ScanEligibility:
    """Classify one tracked path without logging or opening its contents.

    :func:`is_scannable` adapts this result for Typos selection, which remains
    uncapped. :func:`phrase_scan._scan_eligibility_for_path` consumes it for
    path-free logging.

    Parameters
    ----------
    candidate
        Absolute path whose tracked-file eligibility is being checked.
    root
        Resolved repository root that contains eligible files.
    max_file_size_bytes
        Optional size limit; ``None`` leaves the input uncapped for Typos.

    Returns
    -------
    ScanEligibility
        Fixed category and reason for the candidate's eligibility.

    Raises
    ------
    OSError
        If resolving the candidate or reading its filesystem metadata fails.

    Examples
    --------
    >>> root = pathlib.Path(".").resolve()
    >>> scan_eligibility(root / "README.md", root).is_eligible  # doctest: +SKIP
    True
    """
    if candidate.is_symlink():
        return ScanEligibility.SYMLINK
    if not is_inside_worktree(candidate, root):
        return ScanEligibility.OUTSIDE_WORKTREE
    file_stat = os.lstat(candidate)
    if stat.S_ISDIR(file_stat.st_mode):
        return ScanEligibility.DIRECTORY_REPLACEMENT
    if not stat.S_ISREG(file_stat.st_mode):
        return ScanEligibility.NON_REGULAR
    if max_file_size_bytes is not None and file_stat.st_size > max_file_size_bytes:
        return ScanEligibility.OVERSIZED
    return ScanEligibility.ELIGIBLE


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
    selected: list[pathlib.Path] = []
    for relative in relatives:
        try:
            if is_scannable(repository / relative, root):
                selected.append(relative)
        except FileNotFoundError, IsADirectoryError, NotADirectoryError:
            continue
    return tuple(selected)


def _read_bounded_bytes(file_descriptor: int) -> bytes | None:
    """Read at most one byte beyond the input limit to detect growth."""
    content = bytearray()
    read_limit = MAX_FILE_SIZE_BYTES + 1
    while len(content) < read_limit:
        chunk = os.read(file_descriptor, read_limit - len(content))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return None
    return bytes(content)


def _read_open_file(file_descriptor: int, path: pathlib.Path) -> TrackedTextRead:
    """Validate and decode bytes from an already secured descriptor."""
    file_stat = os.fstat(file_descriptor)
    if stat.S_ISDIR(file_stat.st_mode):
        raise IsADirectoryError(path)
    if not stat.S_ISREG(file_stat.st_mode):
        return TrackedTextRead(None, ReadOutcome.NON_REGULAR)
    if file_stat.st_size > MAX_FILE_SIZE_BYTES:
        return TrackedTextRead(None, ReadOutcome.OVERSIZED)

    content = _read_bounded_bytes(file_descriptor)
    if content is None:
        return TrackedTextRead(None, ReadOutcome.OVERSIZED)
    try:
        with io.TextIOWrapper(io.BytesIO(content), encoding="utf-8") as reader:
            text = reader.read()
    except UnicodeDecodeError:
        return TrackedTextRead(None, ReadOutcome.BINARY)
    return TrackedTextRead(text, ReadOutcome.TEXT)


def read_tracked_text(root: pathlib.Path, relative: pathlib.Path) -> TrackedTextRead:
    r"""Read tracked UTF-8 text, reporting binary content as absent.

    Repositories track images, fonts, archives, and compiled artefacts. Such
    content carries no prose for a phrase check to examine, so it is skipped
    rather than failing the scan. Oversized files are also skipped, and the
    opened descriptor is validated before a read capped at one byte beyond the
    limit. A read error is a different matter: the worktree has changed under
    the scan, which must fail closed.

    Parameters
    ----------
    root
        Resolved worktree that anchors the descriptor-relative open.
    relative
        Repository-relative path reported by a failure.

    Returns
    -------
    TrackedTextRead
        Decoded bounded contents and a fixed outcome for the orchestration
        layer to log. The text is absent for oversized, binary, or non-regular
        inputs.

    Raises
    ------
    PhraseScanError
        If the file cannot be read.

    Examples
    --------
    >>> read_tracked_text(  # doctest: +SKIP
    ...     pathlib.Path("/repo"), pathlib.Path("README.md")
    ... ).text
    '# Project\n'
    >>> read_tracked_text(  # doctest: +SKIP
    ...     pathlib.Path("/repo"), pathlib.Path("logo.png")
    ... ).text is None
    True
    """
    try:
        root = root.resolve()
        file_descriptor = open_tracked_file(root, relative)
        if file_descriptor is None:
            return TrackedTextRead(None, ReadOutcome.NON_REGULAR)
        try:
            return _read_open_file(file_descriptor, root / relative)
        finally:
            os.close(file_descriptor)
    except OSError as error:
        raise PhraseScanError(relative) from error
