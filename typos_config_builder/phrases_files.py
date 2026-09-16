"""Select and read the tracked files the phrase scanner may examine.

Deciding which tracked paths carry scannable text is a separate concern from
matching phrases within that text: it deals with Git enumeration, policy
documents, symlinks, submodule gitlinks, and the worktree boundary. Keeping it
here leaves :mod:`typos_config_builder.phrases` to the matching itself.

Selection fails closed: a tracked file that cannot be read or decoded raises
rather than being skipped, because a silent skip hides exactly the files most
likely to have drifted.
"""

from __future__ import annotations

import pathlib
import shutil

# The phrase gate enumerates tracked files through Git by design; the
# command is resolved from PATH and its arguments are never user text.
import subprocess  # noqa: S404
import typing as typ

from typos_config_builder import builder

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


class PhraseScanError(Exception):
    """Report that a tracked file could not be read or decoded.

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
            Repository-relative path that could not be read or decoded.
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
    listing = subprocess.run(  # noqa: S603
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


def is_scannable(candidate: pathlib.Path, root: pathlib.Path) -> bool:
    """Report whether a tracked path is a file lying inside the worktree.

    A directory at a tracked path is an anomaly rather than text, so it is
    excluded from anything handed to an external checker.

    Parameters
    ----------
    candidate
        Absolute path to the tracked file on disk.
    root
        Resolved worktree the path must remain within.

    Returns
    -------
    bool
        True when the path may be submitted as tracked text.

    Examples
    --------
    >>> root = pathlib.Path(".").resolve()
    >>> is_scannable(root / "README.md", root)  # doctest: +SKIP
    True
    """
    return is_inside_worktree(candidate, root) and not candidate.is_dir()


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
    return tuple(
        relative for relative in relatives if is_scannable(repository / relative, root)
    )


def read_tracked_text(path: pathlib.Path, relative: pathlib.Path) -> str:
    r"""Read tracked UTF-8 text, failing closed on any read or decode error.

    Parameters
    ----------
    path
        Absolute path to read.
    relative
        Repository-relative path reported by a failure.

    Returns
    -------
    str
        Decoded file contents.

    Raises
    ------
    PhraseScanError
        If the file cannot be read or decoded as UTF-8.

    Examples
    --------
    >>> read_tracked_text(  # doctest: +SKIP
    ...     pathlib.Path("/repo/README.md"), pathlib.Path("README.md")
    ... )
    '# Project\\n'
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PhraseScanError(relative) from error
