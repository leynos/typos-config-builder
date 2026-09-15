# Developer guide

This guide is for maintainers of the focused `typos-config-builder` package. The
[design](typos-config-builder-design.md), [repository layout](repository-layout.md),
and [scope decision](adrs/0001-keep-the-builder-focused.md) define its
boundaries.

## Implementation contract

The package targets Python 3.14 and exposes its CLI through the
`pyproject.toml` console entry point. Cyclopts parses three commands over
`pathlib` filesystem paths:

- the **default command** refreshes the cache, merges the overlay, and
  renders `typos.toml`, writing it or, with `--check`, reporting drift
  without writing;
- **`check-phrases`** loads the cached and merged policy and scans tracked
  text for `[phrases.corrections]` violations, independent of Typos; and
- **`gate`** composes the two: it runs the default command in write mode,
  then the pinned Typos binary, then the phrase check, and exits with the
  worst of the two checking stages' results.

`cli.py` is the only module every other module is free of: it imports
`builder` for the default command, `gate` for the composed workflow, and
`phrases` for `check-phrases`. Keep policy parsing, cache refresh, overlay
merging, deterministic rendering, and drift checking free from repository
discovery or external-tool orchestration.

### Module dependency edges

The package keeps a small, acyclic dependency graph:

- `cli` imports `builder`, `phrases`, and `gate`.
- `gate` imports `builder` and `phrases`.
- `phrases` imports `builder`, `policy`, `patterns`, and `phrases_files`.
- `builder` imports `cache`, `policy`, and `render`.
- `cache` imports `http` (as a function-local import, to keep the module
  loadable without pulling in the HTTPS stack at import time), and `http`
  imports `remote`. That dependency runs in one direction only, so shared
  helpers belong in `remote.py` rather than being imported back out of
  `http.py`.

`policy`, `patterns`, and `render` import nothing else from the package, so
each stays a leaf that lower-level modules can depend on without risking a
cycle.

### Subprocess owners

Exactly two modules start a process, and each owns a different tool:

- `phrases_files.py` runs `git`, resolving the executable through
  `shutil.which` and closing standard input so a command double cannot wedge
  the gate on an inherited terminal. It lists tracked files and reads their
  text, failing closed as described below; `phrases.py` imports it for both
  and re-exports `tracked_files` so callers keep one import site.
- `gate.py` runs the pinned Typos binary. The console script is resolved
  beside `sys.executable` first and only then from `PATH`, so the pinned
  version wins over an unrelated binary earlier on the search path. Paths are
  submitted in chunks so a large repository cannot exceed the platform's
  command-line length limit, and the worst exit code of every chunk is the
  stage's result.

Neither module imports the other's subprocess call; `gate.py` reaches tracked
files through `phrases.tracked_files`, which delegates to `phrases_files.py`.

### The four-hundred-line rule

Keep each module under four hundred lines; split a module into a sibling
before appending further behaviour once it approaches the limit. Headroom is
thin in two modules: `gate.py` sits at 389 lines, and `remote.py`, which owns
the HTTPS path, the bounded response read, the cache-identity checks, and the
bounded refresh diagnostics, sits at 371 lines. Prefer a new sibling module
over growing either one further. `GateOptions` groups the authority, cache
policy, and scope because `gate` would otherwise exceed the repository's
four-argument limit once the runner seam is included. The runner seam is a
`typ.Protocol` naming only the subset of `subprocess.run` the gate uses, so a
test records invocations without starting a process.

### Fail-closed and worktree-escape rules

The phrase scan fails closed: an unreadable or undecodable tracked file
raises `PhraseScanError` with the cause chained, rather than being skipped,
because a silent skip hides exactly the file most likely to have drifted.
Masking marks every ignored span against the original text and blanks the
marked characters in one pass, which keeps offsets exact and keeps
overlapping spans ignored; a sequential substitution per pattern does not.

A tracked path is skipped, not read, when it is a symlink, when it is a
directory (a submodule gitlink), or when resolving it lands outside the
worktree through a symlinked parent, so the scan can never follow a link out
of the repository it was asked to check.

Typos execution is in scope per the amendment to
[ADR 0001](adrs/0001-keep-the-builder-focused.md): running the pinned Typos
binary and enforcing `[phrases.corrections]` are part of applying the shared
policy consistently, not general-purpose tool orchestration.

The Python implementation accepted in
[Weaver pull request 190](https://github.com/leynos/weaver/pull/190) is the
minimum quality baseline. Subsequent implementation should preserve its typed
boundaries, deterministic output, validated cache behaviour, bounded error
handling, and focused tests. This reference is a floor for implementation
quality, not permission to copy consumer-specific behaviour into the package.

## Change discipline

New behaviour belongs here only when it is necessary to refresh the shared
dictionary cache, combine it with a local overlay, generate `typos.toml`,
check drift, run the pinned Typos binary, or enforce the shared phrase
corrections. Estate inventory, spelling discovery, and other documentation
tooling remain out of scope and belong in their respective repositories or
consumer workflows.

Prefer small tests at stable input and output boundaries. Add regression
coverage for changed behaviour, but do not expand the test matrix speculatively
or reproduce broad consumer integration suites.

## Local quality gates

Use the Makefile targets documented in `AGENTS.md`. Gate changes with the
relevant formatting, lint, type, test, spelling, and audit targets before
commit. The package's own spelling configuration is generated in the same way
as a consumer's configuration; do not edit `typos.toml` by hand.
