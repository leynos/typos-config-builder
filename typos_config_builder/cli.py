"""Expose the focused config-builder command through Cyclopts."""

from __future__ import annotations

import pathlib
import subprocess  # noqa: S404
import sys
import typing as typ

import cyclopts
from cyclopts import App, Parameter

from typos_config_builder import phrases
from typos_config_builder.builder import ConfigDriftError, build

app = App(config=cyclopts.config.Env("TYPOS_CONFIG_BUILDER_", command=False))


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
    except ConfigDriftError as error:
        print(f"drift: {error.output}", file=sys.stderr)
        raise SystemExit(1) from error
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
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
    except (
        OSError,
        ValueError,
        phrases.PhraseScanError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    for finding in findings:
        print(
            f"{finding.path}:{finding.line}:{finding.column}: "
            f"{finding.phrase} -> {finding.correction}"
        )
    if findings:
        raise SystemExit(2)


def main() -> None:
    """Parse command-line arguments and run the config builder.

    Examples
    --------
    >>> main()  # doctest: +SKIP
    """
    app()


if __name__ == "__main__":
    main()
