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
  renders `typos.toml`, writing it or, with `--check`, reporting drift without
  writing;
- **`check-phrases`** loads the cached and merged policy and scans tracked
  text for `[phrases.corrections]` violations, independent of Typos; and
- **`gate`** composes the two: it runs the default command in write mode,
  then the pinned Typos binary, then the phrase check, and exits with the worst
  of the two checking stages' results.

`cli.py` is the only module every other module is free of. It imports `builder`
for the default command, `gate` for the composed workflow, and `phrases` for
`check-phrases`. Keep policy parsing, cache refresh, overlay merging,
deterministic rendering, and drift checking free from repository discovery or
external-tool orchestration.

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
  text, failing closed as described below; `phrases.py` imports it for both and
  re-exports `tracked_files` so callers keep one import site.
- `gate.py` runs the pinned Typos binary. The console script must be
  installed beside `sys.executable`; there is no `PATH` fallback, so an
  unrelated Typos binary elsewhere on `PATH` can never stand in. Paths are
  submitted in chunks so a large repository cannot exceed the platform's
  command-line length limit, and the worst exit code of every chunk is the
  stage's result.

Neither module imports the other's subprocess call; `gate.py` reaches tracked
files through `phrases.tracked_files`, which delegates to `phrases_files.py`.

### The four-hundred-line rule

Keep each module under four hundred lines; split a module into a sibling before
appending further behaviour once it approaches the limit. Headroom is thin in
two modules: `gate.py` sits at 389 lines, and `remote.py`, which owns the HTTPS
path, the bounded response read, the cache-identity checks, and the bounded
refresh diagnostics, sits at 371 lines. Prefer a new sibling module over
growing either one further. `GateOptions` groups the authority, cache policy,
and scope because `gate` would otherwise exceed the repository's four-argument
limit once the runner seam is included. The runner seam is a `typ.Protocol`
naming only the subset of `subprocess.run` the gate uses, so a test records
invocations without starting a process.

### Type-scoped rendering

Available from 0.1.2, `render.py` emits one optional table beyond the fixed
skeleton. When merged policy carries any `markdown_patterns`, a
`[type.markdown]` table is written between the `[default]` array and
`[default.extend-words]`, holding an `extend-glob` of `*.md` and the sorted
confined expressions. When the tuple is empty the table is omitted entirely, so
output stays byte-identical for every repository that does not use
`[patterns] markdown_only`.

The withdrawal happens in `policy.merge`, not in the renderer: the merge
subtracts the merged Markdown set from the unioned default ignore set, so a
confined expression appears in exactly one of the two tables. `remove` applies
to both sets, so an overlay can withdraw a confinement the shared authority
supplied. A further type-scoped table should follow the same split, with the
selection rule in `policy.py` and the emission in a small helper in
`render.py`, so the renderer stays a pure function of normalized policy.

### Fail-closed and worktree-escape rules

A tracked file whose bytes are not UTF-8 is binary as far as a phrase check is
concerned, so `read_tracked_text` returns `None` after logging one bounded
`phrase-scan` decision carrying neither the path nor any content, and the
scanner moves on to the next file. Repositories legitimately track images,
fonts, archives, and compiled artefacts, and failing on them would fail the
gate in any such repository.

The phrase scan still fails closed on a read error: an unreadable tracked file
raises `PhraseScanError` with the `OSError` chained, rather than being skipped,
because that signals the worktree changed under the scan and a silent skip
hides exactly the file most likely to have drifted. Masking marks every ignored
span against the original text and blanks the marked characters in one pass,
which keeps offsets exact and keeps overlapping spans ignored; a sequential
substitution per pattern does not.

Tracked files are enumerated with their index modes, and submodule gitlinks
(mode 160000) are dropped at that point so neither the phrase scan nor the
Typos run ever sees them. A remaining tracked path is skipped, not read, when
it is a symlink or when resolving it lands outside the worktree through a
symlinked parent, so neither stage can follow a link out of the repository it
was asked to check. A tracked file that has been replaced by a directory is an
error, not a skip, because that is a worktree anomaly rather than policy.

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

## Coverage publication

Pull-request CI generates coverage with the ratchet (`with-ratchet: 'true'`)
against the baseline that `coverage-main.yml` writes on pushes to `main`, and
publishes no coverage artefact (`publish-artefact: 'false'`). Nothing a pull
request runs invokes CodeScene, runs `cs-coverage`, receives `CS_ACCESS_TOKEN`,
or names the CodeScene host.

`coverage-main.yml` is the single publisher. It runs on pushes to `main` and on
manual dispatch (automerged changes do not fire push workflows), binds
`CS_ACCESS_TOKEN` on its upload step alone, guards that step on exactly
`env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'` so a dispatch
from another branch cannot upload, uploads with `mode: upload`, and declares a
concurrency group, keyed on the ref and the event, that never cancels: GitHub
keeps one pending run per group, so a newer push replaces an older pending run
and the newest baseline wins, while a dispatch cannot displace a pending push
to main. The uploader pins the CodeScene CLI through its own manifest, so no
checksum input or `CODESCENE_CLI_SHA256` variable is used.

The reason is the call, not the artefact: the CLI talks to CodeScene's API,
whose answers have changed shape and failed every pull request at once, and a
fork cannot read the token anyway. The ratchet applies the same gate from this
repository's own baseline.

### Workflow contract helpers

`tests/test_codescene_coverage_contract.py` holds the rule over this
repository's workflows. `test_codescene_closure_cases.py` and
`test_codescene_publisher_cases.py` beside it drive the same readings over
constructed documents, one breach each, so every clause is shown to catch what
it names. The readings live beside them in `tests/`:

- `workflow_reading.py` parses workflows and local actions with a loader that
  refuses duplicate keys, reads `on:` in scalar, sequence, and mapping form
  under either key, and walks every key and value of a document.
- `workflow_closure.py` computes the pull-request surface: workflows triggered
  by `pull_request`, `pull_request_target`, `pull_request_review`,
  `pull_request_review_comment`, `merge_group`, `issue_comment`, or
  `workflow_run`, or by a push to any branch other than `main`, and every local
  workflow or composite action they reach through `./` or `$/` references. It
  refuses qualified self-calls and local references carrying `@ref`.
- `codescene_reach.py` and `codescene_publisher.py` hold the CodeScene clauses.

The two generic modules know nothing about CodeScene and may be reused by any
workflow contract in this repository. They are test support only: nothing under
`typos_config_builder/` may import them.

## Change discipline

New behaviour belongs here only when it is necessary to refresh the shared
dictionary cache, combine it with a local overlay, generate `typos.toml`, check
drift, run the pinned Typos binary, or enforce the shared phrase corrections.
Estate inventory, spelling discovery, and other documentation tooling remain
out of scope and belong in their respective repositories or consumer workflows.

Prefer small tests at stable input and output boundaries. Add regression
coverage for changed behaviour, but do not expand the test matrix speculatively
or reproduce broad consumer integration suites.

## Local quality gates

Use the Makefile targets documented in `AGENTS.md`. Gate changes with the
relevant formatting, lint, type, test, spelling, and audit targets before
commit. The package's own spelling configuration is generated in the same way
as a consumer's configuration; do not edit `typos.toml` by hand.
