# Developer guide

This guide is for maintainers of the focused `typos-config-builder` package. The
[design](typos-config-builder-design.md), [repository layout](repository-layout.md),
and [scope decision](adrs/0001-keep-the-builder-focused.md) define its
boundaries.

## Implementation contract

The package targets Python 3.14 and exposes its CLI through the
`pyproject.toml` console entry point. The single command writes configuration
by default; `--check` selects non-writing drift detection. Use Cyclopts for
command parsing and `pathlib` for filesystem paths. Keep policy parsing, cache
refresh, overlay merging, deterministic rendering, and drift checking free from
repository discovery or external-tool orchestration.

Refresh is split across two modules to keep each under the four-hundred-line
limit. `remote.py` owns the HTTPS path, the bounded response read, the
cache-identity checks, and the bounded refresh diagnostics. `http.py` owns the
local and offline paths and the `refresh` entry point, and imports `remote`.
The dependency runs in that one direction only, so shared helpers belong in
`remote.py` rather than being imported back out of `http.py`.

`phrases.py` enforces the shared phrase corrections. It imports `builder`
for the policy file names, `policy` for loading and merging, and `patterns` for
compiling ignore expressions; nothing in the package imports it except `cli`,
so it stays a leaf. It is the only module that runs a subprocess, resolving
`git` through `shutil.which` and closing standard input so a command double
cannot wedge the gate on an inherited terminal. Masking marks every ignored
span against the original text and blanks the marked characters in one pass,
which keeps offsets exact and keeps overlapping spans ignored; a sequential
substitution per pattern does not. Reads fail closed: an unreadable or
undecodable tracked file raises `PhraseScanError` with the cause chained,
rather than being skipped. The module stands at 389 lines, so further phrase
behaviour should be extracted into a sibling module rather than appended.

`gate.py` composes the whole gate and is the package's only other module that
starts a process. It imports `builder` for generation and the generated
configuration's name, and `phrases` for the tracked-file listing and the phrase
check; nothing imports it except `cli`, so it stays a leaf alongside
`phrases.py`. The Typos console script is resolved beside `sys.executable`
first and only then from `PATH`, so the pinned version wins over an unrelated
binary earlier on the search path. Paths are submitted in chunks so a large
repository cannot exceed the platform's command-line length limit, and the
worst exit code of every chunk is the stage's result. `GateOptions` groups the
authority, cache policy, and scope because `gate` would otherwise exceed the
repository's four-argument limit once the runner seam is included. The runner
seam is a `typ.Protocol` naming only the subset of `subprocess.run` the gate
uses, so a test records invocations without starting a process.

The Python implementation accepted in
[Weaver pull request 190](https://github.com/leynos/weaver/pull/190) is the
minimum quality baseline. Subsequent implementation should preserve its typed
boundaries, deterministic output, validated cache behaviour, bounded error
handling, and focused tests. This reference is a floor for implementation
quality, not permission to copy consumer-specific behaviour into the package.

## Change discipline

New behaviour belongs here only when it is necessary to refresh the shared
dictionary cache, combine it with a local overlay, generate `typos.toml`, or
check drift. Estate inventory, spelling discovery, Typos execution, and other
documentation tooling belong in their respective repositories or consumer
workflows.

Prefer small tests at stable input and output boundaries. Add regression
coverage for changed behaviour, but do not expand the test matrix speculatively
or reproduce broad consumer integration suites.

## Local quality gates

Use the Makefile targets documented in `AGENTS.md`. Gate changes with the
relevant formatting, lint, type, test, spelling, and audit targets before
commit. The package's own spelling configuration is generated in the same way
as a consumer's configuration; do not edit `typos.toml` by hand.
