"""Expose the focused config-builder command through Cyclopts."""

from __future__ import annotations

import pathlib
import subprocess  # noqa: S404
import sys
import typing as typ

import cyclopts
from cyclopts import App, Parameter

from typos_config_builder import gate as gating
from typos_config_builder import phrases
from typos_config_builder.builder import ConfigDriftError, build

if typ.TYPE_CHECKING:
    import collections.abc as cabc

app = App(config=cyclopts.config.Env("TYPOS_CONFIG_BUILDER_", command=False))

#: Failures every command reports as one concise line rather than a traceback.
EXPECTED_FAILURES = (
    ConfigDriftError,
    OSError,
    ValueError,
    gating.TyposUnavailableError,
    phrases.PhraseScanError,
    subprocess.CalledProcessError,
)


def _exit_with_error(error: Exception) -> typ.NoReturn:
    """Report one expected failure on standard error and exit one.

    Parameters
    ----------
    error
        Expected failure raised by the builder, the phrase scanner, or the
        gate.

    Raises
    ------
    SystemExit
        Always, with exit code one.

    Examples
    --------
    >>> _exit_with_error(ValueError("authority is invalid"))  # doctest: +SKIP
    """
    if isinstance(error, ConfigDriftError):
        print(f"drift: {error.output}", file=sys.stderr)
    else:
        print(f"error: {error}", file=sys.stderr)
    raise SystemExit(1) from error


def _print_findings(findings: cabc.Sequence[phrases.PhraseFinding]) -> None:
    """Print each prohibited phrase as a location and prescribed replacement."""
    for finding in findings:
        print(
            f"{finding.path}:{finding.line}:{finding.column}: "
            f"{finding.phrase} -> {finding.correction}"
        )


@app.default
def run(
    repository: pathlib.Path | None = None,
    source: str | None = None,
    *,
    offline: bool = False,
    check: typ.Annotated[
        bool,
        Parameter(
            negative=False,
            help="Report generated configuration drift without writing output.",
        ),
    ] = False,
) -> None:
    """Refresh and build deterministic en-GB-oxendict configuration.

    Parameters
    ----------
    repository
        Consumer repository containing config-builder inputs and output.
    source
        Local path or HTTPS authority. The live shared dictionary on the
        origin's ``main`` branch is used by default.
    offline
        Require an already-valid local cache when true.
    check
        Report generated-config drift without writing output when true.

    Raises
    ------
    SystemExit
        If configuration drifts or an expected build failure occurs.

    Examples
    --------
    >>> run(pathlib.Path("."), offline=True)  # doctest: +SKIP
    """
    repository = pathlib.Path.cwd() if repository is None else repository
    try:
        result = build(repository, source, offline=offline, check=check)
    except EXPECTED_FAILURES as error:
        _exit_with_error(error)
    print(f"{result.refresh_status}: {result.output}")


@app.command
def check_phrases(repository: pathlib.Path | None = None) -> None:
    """Report shared phrase corrections violated by tracked text.

    The check fails closed: a tracked file that cannot be read or decoded
    is an error rather than a silent skip.

    Parameters
    ----------
    repository
        Repository whose tracked files should be checked. Defaults to the
        current working directory.

    Raises
    ------
    SystemExit
        Two when prohibited phrases are found, one when policy cannot be
        loaded or tracked text cannot be scanned.

    Examples
    --------
    >>> check_phrases(pathlib.Path("."))  # doctest: +SKIP
    """
    repository = pathlib.Path.cwd() if repository is None else repository
    try:
        findings = phrases.find_phrases(repository, phrases.load_policy(repository))
    except EXPECTED_FAILURES as error:
        _exit_with_error(error)
    _print_findings(findings)
    if findings:
        raise SystemExit(gating.FINDINGS_EXIT)


@app.command
def gate(
    repository: pathlib.Path | None = None,
    source: str | None = None,
    *,
    offline: bool = False,
    scope: gating.Scope = "markdown",
) -> None:
    """Generate configuration, run Typos, and enforce phrase corrections.

    The builder always runs in write mode, so a live dictionary edit is picked
    up rather than reported as drift. Typos writes its own findings; phrase
    findings are printed afterwards, and both stages always run.

    Parameters
    ----------
    repository
        Repository to gate. Defaults to the current working directory.
    source
        Local path or HTTPS authority. The live shared dictionary is used by
        default.
    offline
        Require an already-valid local cache when true.
    scope
        Check tracked Markdown only, or every tracked file.

    Raises
    ------
    SystemExit
        Two when Typos or the phrase check reports findings, one when the
        gate cannot run to completion.

    Examples
    --------
    >>> gate(pathlib.Path("."), scope="all")  # doctest: +SKIP
    """
    repository = pathlib.Path.cwd() if repository is None else repository
    options = gating.GateOptions(source=source, offline=offline, scope=scope)
    try:
        result = gating.gate(repository, options)
    except EXPECTED_FAILURES as error:
        _exit_with_error(error)
    print(f"{result.build.refresh_status}: {result.build.output}")
    _print_findings(result.phrase_findings)
    if not result.is_clean:
        raise SystemExit(result.status)


def main() -> None:
    """Parse command-line arguments and run the config builder.

    Examples
    --------
    >>> main()  # doctest: +SKIP
    """
    app()


if __name__ == "__main__":
    main()
