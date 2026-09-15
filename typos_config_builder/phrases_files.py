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

from typos_config_builder import builder

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


def tracked_files(repository: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Return a repository's Git-tracked paths in deterministic order.

    Parameters
    ----------
    repository
        Git worktree to enumerate.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Sorted repository-relative tracked paths.

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
        [executable, "-C", str(repository), "ls-files", "-z"],
        check=True,
        capture_output=True,
        # Git does not read standard input here, but a command double standing
        # in for it might. A shim waiting on an inherited terminal would wedge
        # the gate rather than fail it.
        stdin=subprocess.DEVNULL,
        text=True,
    ).stdout
    return tuple(
        pathlib.Path(relative) for relative in sorted(filter(None, listing.split("\0")))
    )


def is_scannable(candidate: pathlib.Path, root: pathlib.Path) -> bool:
    """Report whether a tracked path is text lying inside the worktree.

    Symlinks are never followed, and submodule gitlinks are directories rather
    than text. A path whose parent is a symlink is neither, yet still resolves
    outside the worktree, so the resolved path is checked as well.

    Parameters
    ----------
    candidate
        Absolute path to the tracked file on disk.
    root
        Resolved worktree the path must remain within.

    Returns
    -------
    bool
        True when the path may be read as tracked text.

    Examples
    --------
    >>> root = pathlib.Path(".").resolve()
    >>> is_scannable(root / "README.md", root)  # doctest: +SKIP
    True
    """
    if candidate.is_symlink() or candidate.is_dir():
        return False
    return candidate.resolve().is_relative_to(root)


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
