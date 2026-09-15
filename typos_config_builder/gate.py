"""Run the complete spelling gate in one command.

The gate performs three ordered stages against a consumer repository:
generate ``typos.toml`` from the live shared dictionary, run the pinned Typos
binary over the selected tracked files, and enforce the shared phrase
corrections that Typos cannot express.

The stages never short-circuit one another. Typos findings do not suppress the
phrase check, because a contributor fixing one class of finding should see the
other in the same run. The gate reports the worst exit code of every stage.
"""

from __future__ import annotations

import dataclasses as dc
import os
import pathlib
import shutil

# The gate runs the pinned Typos console script by design. The executable is
# resolved from the installed environment and its arguments are tracked paths.
import subprocess  # noqa: S404
import sys
import typing as typ

from typos_config_builder import builder, phrases

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Scopes a consumer may submit to Typos.
Scope = typ.Literal["markdown", "all"]

#: Upper bound on paths per Typos invocation, so a large repository cannot
#: exceed the platform's command-line length limit.
CHUNK_SIZE = 500

#: Exit code Typos and this gate use to report findings.
FINDINGS_EXIT = 2

_MARKDOWN_SUFFIX = ".md"


class TyposRunner(typ.Protocol):
    """Start a command and report its completion.

    The protocol names the subset of :func:`subprocess.run` the gate uses, so
    a test can record invocations without starting a process.
    """

    def __call__(
        self,
        args: cabc.Sequence[str],
        /,
        *,
        cwd: pathlib.Path,
        check: bool,
        stdin: int,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one command and return its completion."""


class TyposUnavailableError(RuntimeError):
    """Report that the pinned Typos console script could not be located."""


@dc.dataclass(frozen=True, slots=True)
class GateOptions:
    """Select the authority, cache policy, and scope for one gate run.

    Grouping the three settings keeps :func:`gate` within the repository's
    argument-count limit and gives callers one value to pass on.

    Attributes
    ----------
    source
        Local path or HTTPS authority. The live shared dictionary is used by
        default.
    offline
        Require an already-valid local cache when true.
    scope
        ``markdown`` submits tracked Markdown only; ``all`` submits every
        tracked path and searches hidden files.
    """

    source: str | None = None
    offline: bool = False
    scope: Scope = "markdown"


@dc.dataclass(frozen=True, slots=True)
class GateResult:
    """Describe the outcome of every gate stage.

    Attributes
    ----------
    build
        Cache status and generated-configuration state.
    typos_exit
        Worst exit code reported by the Typos stage.
    phrase_findings
        Prohibited phrases found in tracked text.
    """

    build: builder.BuildResult
    typos_exit: int
    phrase_findings: tuple[phrases.PhraseFinding, ...]

    @property
    def status(self) -> int:
        """Worst exit code observed across the gate's stages.

        Returns
        -------
        int
            Zero when every stage passed, otherwise the worst stage's code.

        Examples
        --------
        >>> result = GateResult(
        ...     builder.BuildResult("current", pathlib.Path("typos.toml"), True),
        ...     0,
        ...     (),
        ... )
        >>> result.status
        0
        """
        return max(self.typos_exit, FINDINGS_EXIT if self.phrase_findings else 0)

    @property
    def is_clean(self) -> bool:
        """Whether every gate stage passed.

        Returns
        -------
        bool
            True when no stage reported a finding or a failure.

        Examples
        --------
        >>> result = GateResult(
        ...     builder.BuildResult("current", pathlib.Path("typos.toml"), True),
        ...     2,
        ...     (),
        ... )
        >>> result.is_clean
        False
        """
        return self.status == 0


def select_files(
    tracked: cabc.Sequence[pathlib.Path], scope: Scope
) -> tuple[pathlib.Path, ...]:
    """Select the tracked paths a scope submits to Typos.

    Parameters
    ----------
    tracked
        Repository-relative tracked paths in the order Git reported them.
    scope
        ``markdown`` for tracked Markdown only, ``all`` for every path.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Selected paths in the order they were given.

    Examples
    --------
    >>> select_files((pathlib.Path("a.md"), pathlib.Path("b.txt")), "markdown")
    (PosixPath('a.md'),)
    """
    if scope == "all":
        return tuple(tracked)
    return tuple(path for path in tracked if path.suffix.lower() == _MARKDOWN_SUFFIX)


def typos_executable() -> pathlib.Path:
    """Locate the pinned Typos console script for the running environment.

    The script installed beside the running interpreter is preferred, so the
    version pinned by this package is used even when an unrelated Typos is
    earlier on the executable search path.

    Returns
    -------
    pathlib.Path
        Path to the Typos console script.

    Raises
    ------
    TyposUnavailableError
        If no Typos console script can be found.

    Examples
    --------
    >>> typos_executable().name.startswith("typos")
    True
    """
    name = "typos.exe" if os.name == "nt" else "typos"
    beside_interpreter = pathlib.Path(sys.executable).parent / name
    if beside_interpreter.is_file():
        return beside_interpreter
    located = shutil.which("typos")
    if located is not None:
        return pathlib.Path(located)
    message = (
        "the pinned typos binary was not found beside "
        f"{sys.executable} or on PATH; reinstall typos-config-builder"
    )
    raise TyposUnavailableError(message)


def run_typos(
    repository: pathlib.Path,
    files: cabc.Sequence[pathlib.Path],
    *,
    hidden: bool,
    runner: TyposRunner = subprocess.run,
) -> int:
    """Run the pinned Typos binary over the selected tracked files.

    Parameters
    ----------
    repository
        Repository used as the working directory, so the relative
        ``--config`` path and the relative file paths both resolve.
    files
        Repository-relative paths to check. An empty selection is a success
        without starting a process.
    hidden
        Check files and directories Typos would otherwise skip as hidden.
    runner
        Seam standing in for :func:`subprocess.run`.

    Returns
    -------
    int
        Worst exit code across the invocations. Typos reports two when it
        finds something to correct.

    Raises
    ------
    TyposUnavailableError
        If the pinned Typos console script cannot be found.

    Examples
    --------
    >>> run_typos(pathlib.Path("."), (), hidden=False)
    0
    """
    if not files:
        return 0
    command = [
        str(typos_executable()),
        "--config",
        builder.OUTPUT_NAME,
        "--force-exclude",
    ]
    if hidden:
        command.append("--hidden")
    worst = 0
    for start in range(0, len(files), CHUNK_SIZE):
        chunk = files[start : start + CHUNK_SIZE]
        completed = runner(
            [*command, *(str(path) for path in chunk)],
            cwd=repository,
            # Findings are an expected outcome, not an error to raise on.
            check=False,
            # Typos reads no standard input, but a command double standing in
            # for it might; an inherited terminal would wedge the gate.
            stdin=subprocess.DEVNULL,
        )
        worst = max(worst, completed.returncode)
    return worst


def gate(
    repository: pathlib.Path,
    options: GateOptions | None = None,
    *,
    runner: TyposRunner = subprocess.run,
) -> GateResult:
    """Generate configuration, run Typos, and enforce phrase corrections.

    The builder always runs in write mode. With a live shared dictionary a
    tracked ``typos.toml`` drifts whenever estate policy changes, so failing
    on that drift would fail every consumer on every dictionary edit.

    Parameters
    ----------
    repository
        Git worktree to gate.
    options
        Authority, cache policy, and scope. Defaults select the live shared
        dictionary and tracked Markdown.
    runner
        Seam standing in for :func:`subprocess.run`.

    Returns
    -------
    GateResult
        Outcome of every stage, including the worst exit code.

    Raises
    ------
    FileNotFoundError
        If ``git`` is absent, or offline mode has no valid cache.
    PhraseScanError
        If a tracked file cannot be read or decoded as UTF-8.
    TyposUnavailableError
        If the pinned Typos console script cannot be found.
    subprocess.CalledProcessError
        If ``git`` cannot enumerate the repository's tracked files.
    ValueError
        If the authority or overlay is invalid or conflicts with policy.

    Examples
    --------
    >>> gate(pathlib.Path("."), GateOptions(offline=True)).is_clean  # doctest: +SKIP
    True
    """
    selected = GateOptions() if options is None else options
    build_result = builder.build(
        repository, selected.source, offline=selected.offline, check=False
    )
    tracked = phrases.tracked_files(repository)
    typos_exit = run_typos(
        repository,
        select_files(tracked, selected.scope),
        hidden=selected.scope == "all",
        runner=runner,
    )
    # The phrase stage runs regardless of the Typos outcome, so one run
    # reports every class of finding.
    findings = phrases.find_phrases(repository, phrases.load_policy(repository))
    return GateResult(build_result, typos_exit, findings)
