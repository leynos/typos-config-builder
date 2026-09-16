# Make typos-config-builder the whole estate spelling gate

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision log`, `Outcomes & retrospective`, `Conformance basis`, and
`Verification plan` must be kept up to date as work proceeds.

Status: IN PROGRESS

## Purpose / big picture

Today every consuming repository in the estate carries its own spelling
tooling: a copied phrase-check script and test, a vendored generator, a
Makefile block naming a Typos version, a builder commit, and a coverage gate.
After this change a consumer owns three things only: an optional
`typos.local.toml` overlay, two `.gitignore` lines, and one command,
`typos-config-builder gate`, which refreshes the live shared dictionary,
renders `typos.toml`, runs the pinned Typos binary, and enforces the shared
phrase corrections. Adding a globally accepted word to the dictionary in
`agent-helper-scripts` reaches every consumer on its next run with no
consumer change.

Success is observable by running, in any consumer checkout:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.0" \
  typos-config-builder gate
```

and seeing `typos.toml` regenerated, Typos run over the tracked Markdown
files, and a non-zero exit with `path:line:col: <phrase> -> <correction>`
lines when a tracked file contains a prohibited phrase such as the
hyphenated form of "handwritten".

The sweep that motivates this work is
`~/docs/typos-config-builder-usage-sweep-2026-09-14.md`; its recommendations
R1 to R14 are the requirement identifiers used below.

## Constraints

- Python 3.14 only, as `pyproject.toml` already declares. Unparenthesized
  multi-type `except A, B:` is valid there and is the house style.
- The authoritative shared dictionary is the current `main` of
  `leynos/agent-helper-scripts`, at
  `https://raw.githubusercontent.com/leynos/agent-helper-scripts/refs/heads/main/data/typos-oxendict-base.toml`.
  The builder must never require a release or consumer change to pick up a
  dictionary edit (owner decision, 2026-09-14).
- No consumer-facing script, Makefile include, or per-language variant may be
  introduced; every knob is a tested builder option (owner goal, 2026-09-14).
- Every source module stays under 400 lines. Docstring coverage stays at
  100 percent. All quality gates in `AGENTS.md` pass before each commit.
- The renderer's determinism and the `[phrases.corrections]` non-rendering
  rule are preserved: phrases are enforced by the gate, never written to
  `typos.toml`.
- Test files must not contain the literal prohibited phrase; build it from
  parts as the existing tests do.
- Do not commit `.typos-oxendict-base.*` or any cache file.

## Tolerances (exception triggers)

- Scope: a builder milestone that needs more than 12 changed files or a new
  source module beyond those named in `Plan of work` stops and escalates.
- Dependencies: only `typos` and `pathspec` may be added as runtime
  dependencies. Any other new dependency stops and escalates.
- Interface: renaming or removing the existing default command's options
  (`--repository`, `--source`, `--offline`, `--check`) stops and escalates.
- Iterations: three failed attempts at a green gate for one milestone stops
  and escalates.
- Publishing: creating a PyPI project or trusted-publisher configuration
  needs the owner; the plan stops at a git tag if that is unavailable.
- Consumer batches: any consumer PR that needs a change outside the files
  named in its batch recipe stops and is reported rather than improvised.

## Risks

- Risk: the live authority is unreachable in CI on a fresh clone.
  Severity: medium. Likelihood: low.
  Mitigation: the bundled snapshot becomes a logged bootstrap fallback used
  only when there is no valid cache and the authority cannot be reached (M1).
- Risk: the Typos wheel on PyPI is not available for a platform a consumer
  builds on.
  Severity: medium. Likelihood: low.
  Mitigation: `typos==1.48.0` publishes manylinux, macOS and Windows wheels;
  confirmed by `uv pip install --dry-run` on 2026-09-14. The gate reports a
  clear error naming the missing binary rather than a traceback.
- Risk: relaxing the regex-safety rule admits a genuinely dangerous pattern.
  Severity: high. Likelihood: low.
  Mitigation: only a group quantified by `?` or `{0,1}` stops counting as
  compounding repetition; `(?:a+)+`, `(a|aa)+` and the existing rejection
  set stay rejected and are regression cases (M2).
- Risk: absorbing the phrase gate changes behaviour for repositories that
  today fail open on unreadable files.
  Severity: low. Likelihood: certain.
  Mitigation: fail-closed is the recorded decision; the sweep lists the
  affected repositories so their first `gate` run is reviewed.
- Risk: consumer migration PRs number around sixty and drift in shape.
  Severity: medium. Likelihood: medium.
  Mitigation: one recipe per cohort in `Plan of work`, executed by journeymen
  with explicit file ownership, and a scrutineer gate per PR.

## Progress

- [x] (2026-09-14 17:30Z) Sweep report written and decisions recorded.
- [x] (2026-09-14 18:05Z) Baseline `make all` run on `a343fe4` (see
  `Artefacts and notes`).
- [x] (2026-09-14 23:40Z) EP-M1 live authority default with bundled
  bootstrap fallback: `DEFAULT_SOURCE`, `bundled_authority()`,
  `RefreshOptions.bootstrap`, `cache.bootstrap_cache`, tests, docs, ADR
  amendment, and this repository's write-mode `spelling` target.
- [x] (2026-09-15 01:20Z) EP-M2 regex-safety relaxation for optional
  groups: `patterns._is_at_most_once`, the relaxed
  `_consume_atom_or_operator` guard, and `tests/test_patterns.py` with
  the INV-3 corpora and two Hypothesis properties.
- [x] (2026-09-15 03:05Z) EP-M3 hardening: 429 added to
  `TRANSIENT_HTTP_STATUSES`, the `MAX_AUTHORITY_BYTES` response cap, the
  `[patterns] remove` overlay key, and the extraction of the HTTPS path into
  `typos_config_builder/remote.py`.
- [x] (2026-09-15 06:20Z) EP-M4 `check-phrases` command:
  `typos_config_builder/phrases.py`, the Cyclopts `check-phrases` command,
  the `pathspec` dependency, `tests/test_phrases.py`, three command-boundary
  tests, the users' guide section, and this repository's `spelling` target.
- [x] (2026-09-15 09:40Z) EP-M5 `gate` command with pinned Typos and
  `--scope`: `typos_config_builder/gate.py`, the Cyclopts `gate` command, the
  shared CLI error translation, the `typos==1.48.0` dependency,
  `tests/test_gate.py`, two command-boundary tests, the users' guide gate
  section, and this repository's one-line `spelling` target.
- [x] (2026-09-15 17:20Z) EP-M6 fork tests ported, docs and ADR amended.
  Ported 17 tests from weaver, ortho-config and mriya into
  `tests/test_remote.py`, `tests/test_policy.py` and `tests/test_build.py`
  (HTTPS-only source and redirects, 304 digest binding, invalid body keeps
  cache, ETag precedence, date fallback, non-object metadata, an
  11-row malformed-policy matrix, sparse-overlay and merge semantics).
  Not ported: two pre-digest-binding tests, mriya's silent-skip race test
  (builder fails closed), fork scaffolding tests, and ortho-config's
  10-row broad-exception matrix, which remains a small open gap. Full
  gate: 134 passed. Tag `v0.1.0` follows the builder PR merge.
  (2026-09-15 17:30Z) PR leynos/typos-config-builder#69 opened from this
  branch; CodeRabbit review queued via comenq. PR
  leynos/agent-helper-scripts#152 is green on CI at `daa2356` with a fresh
  review queued (7956e7ab).
  (2026-09-15 17:50Z) First review round on #69 (3 Codex, 17 CodeRabbit
  findings) answered in `3008a92`: 17 fixed, 3 skipped as suite-wide
  style (bare asserts, NumPy sections on test helpers) with reasons on
  the threads; the dependency audit is fixed by pinning pip in the dev
  group.
- [ ] EP-M7 agent-helper-scripts: style-guide patterns and docs pointing
  at the builder. (2026-09-14 22:35Z) PR leynos/agent-helper-scripts#152
  opened from worktree `feature/typos-shared-patterns`; CodeRabbit review
  queued (comenq 03e6db1f, ETA about nine hours); merge on green pending.
  The inline-code pattern was withdrawn from this milestone (see Decision
  log).
- [ ] EP-M8 cohort A consumers (28 repos).
- [ ] EP-M9 cohort B1 consumers (22 repos).
- [ ] EP-M10 templates and cohort B2 (2 templates, 7 repos).
- [ ] EP-M11 cohort B3 consumers (7 repos).
- [ ] EP-M12 retire the origin's generator.

## Surprises & discoveries

- Observation: the live authority already differs from the bundled snapshot.
  Regenerating `typos.toml` from `main` added four correction families and
  removed the inline-code ignore pattern `` `[^`\n]+` ``, which exists only in
  the bundled snapshot. This repository's own Markdown then failed on two
  legitimate inline-code spans quoting external API identifiers (`color` in
  the documentation style guide, `artifact` in the act guide).
  Response: the pattern was added to this repository's `typos.local.toml`
  with a comment pointing at EP-M7, which moves it into the shared base. The
  overlay entry should be removed once EP-M7 lands, since `policy.merge`
  unions patterns and would otherwise keep a redundant local copy.
  Implication for consumers: every consumer whose first run uses the live
  authority loses that pattern until EP-M7 ships, so EP-M7 should precede the
  consumer batches EP-M8 to EP-M12.
- Observation: the scanner already accepts Python's open-lower-bound form
  `a{,3}b`, which is bounded repetition and therefore safe. EP-M2's reject
  corpus was drafted with that pattern as a candidate rejection; the checked
  behaviour was kept instead and the pattern now sits in the accept corpus as
  a regression guard.
- Observation: the test source for EP-M2 quotes consumer overlay patterns
  that exist precisely to protect US or product spellings, so the file failed
  this repository's own Typos run. The four offending words are spliced from
  fragments, following the convention already used in `tests/test_build.py`.
  EP-M5's `gate --scope all` will subject every Python file to the same
  treatment, so the convention should be documented before that lands.
- Observation: moving the refresh diagnostics into `remote.py` changed the
  logger name for the bootstrap decision from `typos_config_builder.http` to
  `typos_config_builder.remote`. The bootstrap test asserted on the former, so
  it now captures at the package logger `typos_config_builder`, which covers
  both modules through level inheritance and does not have to move again if the
  decision moves module.
- Observation: giving the newly cross-module helpers in `remote.py` full
  numpydoc sections pushed the module to 461 lines, over the four-hundred-line
  limit. The helpers are package-internal rather than public API, so they were
  returned to the one-line docstring style the repository already uses for
  module-private helpers; only `refresh_https`, the module's entry point, keeps
  a full docstring. `remote.py` stands at 364 lines.
- Observation: `policy.Dictionary` normalizes every list by sorting it, so a
  gitignore re-inclusion such as `!README.md` is ordered before the `*.md` it
  was written to qualify. Gitignore gives the last matching pattern priority,
  so the re-inclusion is inert, both in `check-phrases` and in the generated
  `extend-exclude` that Typos itself reads.
  Response: EP-M4 mirrors the generated configuration rather than inventing a
  different order, so the gate and Typos agree. The behaviour is documented in
  the users' guide and pinned by
  `tests/test_phrases.py::test_excluded_globs_apply_in_normalized_order`.
  Implication: this predates EP-M4 and affects every consumer that wrote a
  re-inclusion. Preserving author order would change rendered output and so
  touches INV-1; it needs an owner ruling rather than a quiet fix, and is
  raised as an open question rather than actioned here.
- Observation: the packaged snapshot `typos_config_builder/data/
  typos-oxendict-base.toml` is itself a policy document listing the prohibited
  phrases, so the first `check-phrases` run in this repository reported shared
  policy as a finding. `POLICY_PATHS` cannot name it, because that path is
  specific to this repository and no consumer carries it.
  Response: this repository's `typos.local.toml` excludes the snapshot, which is
  the designed repository-specific knob. `agent-helper-scripts` solves the same
  problem the same way in its own vendored checker.
- Observation: the packet's `gate(repository, *, source, offline, scope,
  runner)` signature has five parameters, and both Ruff's `PLR0913` and Pylint
  are configured here with `max-args = 4`, so it was rejected by
  `uv run ruff check` before any test ran.
  Response: the authority, cache policy, and scope moved into a frozen
  `gate.GateOptions`, following the `cache.RefreshOptions` and
  `cache.BootstrapRequest` precedent recorded on 2026-09-14 23:58Z. The
  delivered signature is `gate(repository, options=None, *, runner=...)`. The
  `cli.gate` command signature named in `Interfaces and dependencies` is
  unchanged, so the consumer-facing contract is exactly as planned.
- Observation: Ruff rejects a property docstring that opens with a verb, so
  `GateResult.status` and `GateResult.is_clean` document themselves as noun
  phrases rather than in the `Return ...` style the module's functions use.
- Observation: one run of `uv run pytest tests/test_cli.py` stalled for more
  than thirty seconds inside `git init` in the pre-existing
  `test_check_phrases_reports_a_missing_cache` fixture and was killed by
  `pytest-timeout`. The identical command immediately afterwards reported
  `10 passed` in 18 seconds, and the stall was in a fixture untouched by this
  milestone. It is recorded as an environment hiccup, not a contract change;
  if it recurs, the fixture's Git invocations are the place to look.
- Observation: `phrases.py` stands at 389 lines with full numpydoc sections, so
  it has almost no headroom under the four-hundred-line limit. EP-M5 puts the
  Typos runner in a separate `gate.py`, but any further phrase behaviour should
  be extracted into a sibling module rather than appended. The developers' guide
  records this.
- Observation: `http.py` reached 402 lines when the bootstrap fallback was
  added inline. The snapshot write now lives in `cache.bootstrap_cache` and
  `http.py` stands at 399 lines, leaving almost no headroom for EP-M3's 429
  status and size cap. EP-M3 should plan to extract the remote-response path
  rather than append to `http.py`.
- Observation: `run_typos` aggregated chunk exit codes with `max()`, which
  silently discards a negative return code. A Typos process killed by a signal
  therefore contributed zero, so a killed run over a repository with no phrase
  findings reported a clean gate and exit zero.
  Response: any negative return code is now an operational failure. `run_typos`
  raises `gate.TyposFailedError` naming the signal. The class derives from
  `OSError` so `cli.EXPECTED_FAILURES` already translates it into one
  `error: ...` line and exit one, without widening that tuple.
  Pinned by `tests/test_gate.py::test_run_typos_rejects_a_signalled_exit` and
  `::test_signalled_typos_exits_one_through_the_cli`.
- Observation: `git ls-files -z` lists submodule gitlinks, which are
  directories on disk rather than symlinks. `find_phrases` skipped only
  symlinks, so `read_text` raised `IsADirectoryError` and the scan failed
  closed on every consumer repository carrying a submodule.
  Response: `find_phrases` skips a tracked path that is a directory alongside
  the existing symlink skip. A vanished tracked file is neither, so the
  fail-closed contract for a removed file is unchanged.
  Pinned by the `test_tracked_submodule_directory_is_skipped` case in
  `tests/test_phrases.py`, which records the gitlink with
  `git update-index --cacheinfo` rather than adding a real submodule, avoiding
  an inner commit and relaxed file-protocol settings.

## Decision log

- Decision: the live origin `main` is the default `--source`; the bundled
  file stays as a bootstrap fallback only.
  Rationale: owner decision; bundling turned a one-word change into 28 pin
  bumps that never happened.
  Date/Author: 2026-09-14, repository owner.
- Decision: the bundled snapshot is offered as a bootstrap only when the
  caller selects no source; an explicitly chosen authority still fails loudly.
  Rationale: the snapshot stands in for the default authority alone. Offering
  it for a caller-selected source would silently substitute unrelated policy
  and would weaken the existing offline `FileNotFoundError` contract.
  Date/Author: 2026-09-14, EP-M1 implementation.
- Decision: the builder runs Typos itself, pinned as a Python dependency.
  Rationale: owner goal of near-zero consumer code; `typos==1.48.0` is a
  PyPI binary wheel. ADR 0001 is amended in M6 to say running Typos and
  enforcing phrases are part of applying shared policy.
  Date/Author: 2026-09-14, repository owner.
- Decision: the phrase gate fails closed on unreadable or undecodable
  tracked files, skips symlinks, and uses mark-then-blank masking.
  Rationale: union of the fourteen consumer variants minus framework
  choices; fail-open hid errors and three repositories had already moved to
  fail-closed.
  Date/Author: 2026-09-14, lead session.
- Decision: `gate` runs the builder in write mode, never `--check`.
  Rationale: with a live authority a tracked `typos.toml` checked in CI would
  fail in every consumer on every dictionary edit.
  Date/Author: 2026-09-14, repository owner.
- Decision: publish releases as git tags consumed via
  `git+https://...@vX.Y.Z`; PyPI publication is deferred until the owner
  provides a trusted-publisher configuration.
  Rationale: the release workflow only uploads wheels to GitHub Releases and
  creating a PyPI project needs the owner's account.
  Date/Author: 2026-09-14, lead session.

- Decision: withdrawing a pattern the shared base does not contain is a
  harmless no-op, not an error. This supersedes the `Plan of work` text for
  EP-M3, which said removal of an absent pattern should be rejected.
  Rationale: the origin's `typos_rollout_merge._merge_ignore_patterns` performs
  a plain set subtraction, and the origin's users' guide states the no-op
  contract explicitly. Rejecting would break every consumer overlay the moment
  shared policy retired a pattern, which is precisely when the overlay is
  least able to respond.
  Date/Author: 2026-09-15, EP-M3 implementation.

- Decision: the inline-code ignore pattern is not pushed upstream in EP-M7.
  Rationale: the origin's own tests and users' guide assert that inline code
  is checked, so masking it estate-wide is a policy change rather than a
  sync. The bundled snapshot has carried the pattern since the first builder
  commit, so cohort A repositories have masked inline code for two months
  while the 36 legacy repositories have not. Pending owner ruling: either
  add the pattern to the shared dictionary (and change the origin's tests)
  or drop it from the snapshot and let repositories that need it carry one
  overlay line. Until ruled, this repository keeps the overlay entry and
  EP-M7 ships only the two style-guide patterns.
  Date/Author: 2026-09-14, lead session.

## Outcomes & retrospective

To be completed at each milestone boundary.

EP-M4: the phrase gate landed as a single leaf module with no change to the
existing default command. The two behaviours worth carrying forward are the
mark-then-blank masking, which only an explicit overlapping-span example can
falsify, and failing closed on unreadable tracked files, which immediately
surfaced a real finding in this repository's own packaged snapshot. One open
question is left for the owner: normalization sorts file exclusions, which makes
gitignore re-inclusion inert estate-wide. EP-M4 preserved the existing
behaviour rather than diverging from the generated configuration.

## Context and orientation

The package `typos_config_builder/` has eleven modules, at the time of
writing (a journeyman may add one more helper module today). `cli.py`
exposes the Cyclopts default command, `check-phrases`, and `gate`, and calls
`builder.build` for the first. `builder.py` runs the pipeline: `cache.refresh`
(delegating to `http.refresh`) refreshes the cache file
`.typos-oxendict-base.toml` and its metadata sidecar
`.typos-oxendict-base.json`; `policy.load` and `policy.merge` produce a
`Dictionary`; `render.render` produces `typos.toml` text; `cache.atomic_write`
writes it. `patterns.py` validates ignore regexes and local file exclusions.
`remote.py` owns the HTTPS path, the bounded response read, and the
cache-identity checks that `http.py` builds `cache.refresh` on top of.
`gate.py` composes the default command, the pinned Typos binary, and the
phrase check, and exits with the worst of the two checking stages' results.
`phrases.py` loads the cached and merged policy and scans tracked text for
`[phrases.corrections]` violations, independent of Typos. `phrases_files.py`
resolves `git` through `shutil.which` and lists and reads tracked files,
which `phrases.py` re-exports as `tracked_files` for `gate.py` to share.
`data/typos-oxendict-base.toml` is the bundled snapshot of the shared
dictionary. Tests live in `tests/` (`test_build.py`, `test_cli.py`,
`test_http.py`, `conftest.py`) and run with `make test`.

"Phrase corrections" are entries in `[phrases.corrections]` such as
the hyphenated form of "handwritten" mapped to the closed compound. Typos
tokenizes on punctuation, so it cannot
enforce them; the builder carries them in `Dictionary.phrase_corrections` but
does not render them.

The reference consumer implementations to draw from are read-only:
`/data/leynos/Projects/whitaker/scripts/typos_rollout_check.py` (mark-then-
blank masking), `/data/leynos/Projects/wireframe/scripts/typos_rollout_check.py`
and its test (symlink skip, Hypothesis properties),
`/data/leynos/Projects/mriya/scripts/tests/test_typos_rollout_check.py`
(malformed-policy matrix, race handling), and
`/data/leynos/Projects/agent-helper-scripts/scripts/typos_rollout_merge.py`
(`patterns.remove`).

## Conformance basis

Upstream artefacts: `docs/typos-config-builder-design.md` (Accepted),
`docs/adrs/0001-keep-the-builder-focused.md` (Accepted 2026-07-14, to be
amended in M6), `agent-helper-scripts/docs/adr/003-shared-oxford-spelling-base.md`
(Accepted 2026-07-10), and the sweep report's recommendations R1 to R14.
There is no Terms of Reference document.

Trace chain:

```plaintext
R1  -> EP-M1 -> tests/test_build.py::test_default_source_is_live_authority
R1  -> EP-M1 -> tests/test_http.py::test_unreachable_authority_without_cache_bootstraps_from_bundle
R3  -> EP-M2 -> tests/test_patterns.py::test_optional_group_with_inner_repetition_is_accepted
R5a -> EP-M3 -> tests/test_http.py::test_rate_limited_status_uses_stale_cache
R5a -> EP-M3 -> tests/test_http.py::test_oversized_authority_is_rejected
R5a -> EP-M3 -> tests/test_build.py::test_local_patterns_remove_withdraws_shared_pattern
R6  -> EP-M4 -> tests/test_phrases.py::*
R6a -> EP-M5 -> tests/test_gate.py::*
R7  -> EP-M5 -> tests/test_gate.py::test_scope_all_includes_non_markdown
R5  -> EP-M6 -> ported tests listed in Progress
R2  -> EP-M7 -> agent-helper-scripts PR
R9..R14 -> EP-M8..M12 -> per-repository PRs recorded in Progress
```

## Verification plan

Invariants introduced or preserved:

- INV-1 Determinism: for a fixed cache and overlay, `render.render` output is
  byte-identical across runs. Preserved from today; existing test
  `test_build_is_deterministic_and_leaves_no_temporary_file`.
- INV-2 Bootstrap only when necessary: the bundled snapshot is written to the
  cache only when no cache matching the selected source exists and the
  authority cannot be reached (or `--offline` is set with no cache). A valid
  cache is never replaced by the bundle.
  Method: parameterized tests over the four combinations (cache valid or
  not) x (authority reachable or not) using the injectable opener.
  Non-vacuity: the "cache valid, authority unreachable" case must yield
  `stale-cache`, not `bootstrap`; a seeded fault that always bootstraps must
  fail that case.
- INV-3 Regex safety: after M2 every pattern previously rejected for nested
  unbounded repetition is still rejected; patterns whose only "nesting" is a
  `?`-quantified group are accepted.
  Method: parameterized tests over an explicit accept and reject corpus, and
  a Hypothesis property generating `(?:X)?` around random safe bodies and
  `(?:X+)+` around random bodies.
  Non-vacuity: the reject corpus includes `(?:a+)+`, `(a|aa)+`, `(a*)*`; the
  accept corpus includes the two real overlay patterns from `gauss` and
  `df12-www`.
- INV-4 Phrase gate offsets: masking preserves every line and column so a
  finding's location is exact, and a match inside an ignored span is never
  reported.
  Method: Hypothesis property (ported from `wireframe`) generating text with
  the phrase placed inside and outside generated ignored spans.
  Non-vacuity: a mutation that masks with `re.sub` sequentially must fail
  the overlapping-span case.
- INV-5 Phrase gate boundaries: a phrase is reported only when not adjacent
  to a word character or hyphen on either side, case-insensitively.
  Method: Hypothesis property over generated neighbour characters.
- INV-6 Fail closed: an unreadable or undecodable tracked file, or a `git`
  failure, makes the gate exit non-zero with an error, never exit 0.
  Method: parameterized tests with a deleted tracked file, a non-UTF-8 file,
  and a failing `git`.
- INV-7 Gate ordering: `gate` renders before running Typos, and runs the
  phrase check even when Typos reports findings, returning non-zero if any
  stage fails.
  Method: unit tests with an injected Typos runner recording call order.

Axioms: Typos 1.48.0 honours `--config`, `--force-exclude` and `--hidden` as
documented; `pathspec.GitIgnoreSpec` implements gitignore semantics; `git
ls-files -z` lists tracked paths NUL-separated; `urllib` raises
`urllib.error.HTTPError` with `.code` for HTTP statuses.

## Plan of work

### EP-M1 Live authority default with bundled bootstrap

In `builder.py` replace `_bundled_authority()` as the default with
`DEFAULT_SOURCE` set to the origin URL, and keep `_bundled_authority()` for
the fallback. In `http.py`, when `_stale_cache_or_raise` would raise and the
selected source is HTTPS, or when offline mode finds no cache, write the
bundled bytes to the cache with metadata `{"source": <selected source>,
"sha256": digest, "bootstrap": true}` and return
`RefreshResult("bootstrap", cache)` at WARNING level via `_log_decision`. The
metadata records the selected source so a later successful refresh replaces
it normally. Update `docs/users-guide.md`, `README.md` and the design doc to
name the live authority and the bootstrap rule. Update the Makefile
`spelling` target here to write mode plus running Typos (this repository is
also a consumer). Red tests first in `tests/test_build.py` and
`tests/test_http.py`.

### EP-M2 Regex safety relaxation

In `patterns.py`, `_consume_atom_or_operator` currently treats `?` after a
closed ambiguous group as compounding. Change `note_repetition` callers so a
`?` or `{0,1}` quantifier records the group as an atom but does not raise
`is_unsafe` for `repeats_ambiguous_group`; it still counts for the
adjacent-repetition rule. Add `tests/test_patterns.py` with the corpora in
INV-3.

### EP-M3 Hardening

`http.py`: add 429 to `TRANSIENT_HTTP_STATUSES`; add `MAX_AUTHORITY_BYTES =
10 * 1024 * 1024` and reject a larger body in `_write_remote_cache` with
`ValueError` before validation (read with `response.read(limit + 1)`).
`policy.py`: add `removed_patterns: tuple[str, ...]` to `Dictionary`, parse
`[patterns] remove` in `_from_text` (sparse overlays only), and in `merge`
subtract removed patterns from the union; reject an overlay that both ignores
and removes the same pattern, and reject removal of a pattern the base does
not have. Tests in `tests/test_http.py` and `tests/test_build.py`.

### EP-M4 `check-phrases`

New module `typos_config_builder/phrases.py` (under 400 lines) with
`PhraseFinding`, `PhrasePolicy`, `load_policy(repository) -> PhrasePolicy`
(via `policy.load` and `policy.merge`, reusing the cache and overlay names in
`builder.py`), `tracked_files(repository) -> tuple[Path, ...]` (`git -C repo
ls-files -z`, `stdin=DEVNULL`, `check=True`), `mask(text, patterns)` using
mark-then-blank, `find_phrases(repository, policy) -> tuple[PhraseFinding,
...]` skipping symlinks and policy files and excluded paths via
`pathspec.GitIgnoreSpec`, failing closed on read errors. `cli.py` gains a
`check-phrases` command printing `path:line:col: phrase -> correction` and
exiting 2 on findings, 1 on errors. Add `pathspec>=1.1.1,<2` to
dependencies. Tests in `tests/test_phrases.py` using a `git init` fixture.

### EP-M5 `gate`

New module `typos_config_builder/gate.py`: `run_typos(repository, files,
config, *, hidden, runner)` invoking the `typos` console script from the
current environment (`shutil.which("typos")` next to `sys.executable`) with
`--config typos.toml --force-exclude` and the file list in chunks of 500;
`select_files(tracked, scope)` where `scope` is `markdown` (`*.md`) or
`all`; `gate(repository, *, source, offline, scope) -> int` calling
`build` (write mode), then Typos, then phrases, returning the worst exit
code. `cli.py` gains `gate` with `--scope` defaulting to `markdown`. Add
`typos==1.48.0` to dependencies. Tests inject a fake runner; one marked
`slow` test runs the real binary over a fixture file with a known typo.

### EP-M6 Ported tests, docs, ADR, tag

Port from the forks the tests classified "gap worth porting" that cover
behaviour now in the builder (HTTP edge cases from `weaver` and
`ortho-config`, malformed-policy matrix from `mriya`, properties from
`wireframe`). Amend ADR 0001 and the design doc: running Typos and the phrase
gate are in scope; crawling, harvesting and unrelated orchestration are not.
Rewrite `docs/users-guide.md` around `gate`. Bump `pyproject.toml` version to
`0.1.0`, tag `v0.1.0` after merge to `main`.

### EP-M7 agent-helper-scripts

One PR: add the inline-code ignore pattern and the two style-guide phrase
protections to `data/typos-oxendict-base.toml`, and update its users' guide
and ADR 003 to say consumption is through `typos-config-builder gate`.

### EP-M8 to EP-M12 consumer batches

Recipes are in the sweep report (R9 to R14). Each consumer PR: replace the
spelling Makefile block with the single `gate` target pinned to `v0.1.0`,
delete vendored scripts and tests, drop `PATHSPEC_VERSION`, `TYPOS_VERSION`,
`TYPOS_CONFIG_BUILDER_COMMIT`, `spelling-helper-test`, ensure `.gitignore`
lines, regenerate `typos.toml`, and update the developers' guide paragraph.
Journeymen own repositories; scrutineers run gates; the lead merges on
green after a CodeRabbit review through comenq.

## Milestones and plateaus

- EP-M1: default source is live; bootstrap fallback tested; all gates green;
  this repository's own `make spelling` uses write mode. Recovery: revert the
  commit. Remaining: everything else.
- EP-M2: optional-group patterns accepted; reject corpus green. Recovery:
  revert. Remaining: M3 onward.
- EP-M3: 429, size cap, `patterns.remove` shipped with tests. Recovery:
  revert. Remaining: M4 onward.
- EP-M4: `check-phrases` usable in this repository's Makefile. Recovery:
  revert; the old Makefile still works.
- EP-M5: `gate` usable; this repository's `spelling` target is one `gate`
  call. Recovery: revert.
- EP-M6: docs and ADR consistent; `v0.1.0` tagged. Recovery: retag.
- EP-M7 to EP-M12: each consumer PR is its own plateau.

Compatibility decision: none. The package has no release tag, so no
compatibility machinery is permitted or needed.

## Concrete steps

Working directory:
`/data/leynos/Projects/typos-config-builder.worktrees/audit-missing-functionality`.

```bash
make all            # check-fmt, lint, typecheck, test, spelling
make markdownlint   # after any docs change
make nixie          # only if a Mermaid diagram changes
```

Commit after each milestone with a message that names the milestone, and
open one PR for the builder work at the end of M6 (or earlier if the diff
grows past review size).

## Validation and acceptance

Each milestone records red and green evidence here.

- M1 red (2026-09-14 23:15Z): `uv run pytest tests/test_http.py
  tests/test_build.py -q` with the new tests marked
  `xfail(strict=True)` reports `1 failed, 20 passed, 8 xfailed`. The eight
  xfails are the bootstrap and default-source contracts; the failure is
  `test_bundled_authority_contains_handwritten_policy` with
  `AttributeError: module 'typos_config_builder.builder' has no attribute
  'bundled_authority'`.
- M1 green (2026-09-14 23:35Z): with the markers removed,
  `uv run pytest tests/test_http.py tests/test_build.py -q` reports
  `29 passed`, and `uv run pytest tests/test_cli.py -q` reports `5 passed`.
  `uv run typos-config-builder --repository .` reports `current: typos.toml`
  and the pinned Typos 1.48.0 run over tracked Markdown exits 0. The full
  `make all` gate is run by the lead.
- M2 red (2026-09-15 01:05Z): `uv run pytest tests/test_patterns.py -q` with
  the six compounding accept-corpus parameters and the optional-wrapper
  property marked `xfail(strict=True)` reports `13 passed, 7 xfailed`. Each
  xfail is `ValueError: ignore pattern has unsafe repetition`.
- M2 green (2026-09-15 01:15Z): with the markers removed the same command
  reports `20 passed`, and `uv run pytest tests/test_build.py -q` reports
  `13 passed`, so `test_broad_file_glob_equivalents_are_rejected` is intact.
  Non-vacuity: `--hypothesis-show-statistics` records both branches of the
  optional-wrapper event (54% without an inner repetition, 40% with one).
  Negative control: mutating `_is_at_most_once` to `return False` turns the
  green run into `7 failed, 13 passed`, the six compounding accept-corpus
  parameters plus the property. The mutation was applied by hand, observed,
  and reverted; the test file only describes it.
  `uv run ruff check typos_config_builder tests` and
  `uv run ruff format typos_config_builder tests` are clean, and
  `uv run typos --config typos.toml` over both changed files finds nothing.
  The full `make all` gate is run by the lead.
- M3 red (2026-09-15 02:30Z): with the three `[patterns] remove` contracts
  marked `xfail(strict=True)`, `uv run pytest tests/test_build.py -q` reports
  `13 passed, 3 xfailed`; with the 429 and size-cap contracts marked the same
  way, `uv run pytest tests/test_http.py -q` reports `16 passed, 3 xfailed`.
- M3 green (2026-09-15 03:00Z): after implementation the same files report
  `16 passed` and `19 passed` with every marker removed, and
  `uv run pytest tests/test_cli.py -q` and `tests/test_patterns.py -q` report
  `5 passed` and `20 passed`. Non-vacuity: leaving the markers in place turns
  the green run into three strict `XPASS` failures, which is how the
  implementation was confirmed to be the cause of the change. `ruff format`,
  `ruff check`, `ty check`, and `interrogate --fail-under 100` over
  `typos_config_builder tests` all pass. `http.py` is 157 lines and the new
  `remote.py` is 364 lines. The full `make all` gate is run by the lead.
- M4 red (2026-09-15 05:05Z): with `typos_config_builder/phrases.py` present
  only as signatures raising `NotImplementedError` and the new contracts marked
  `xfail(strict=True)`, `uv run pytest tests/test_phrases.py -q` reports
  `14 xfailed` and `uv run pytest tests/test_cli.py -q` reports
  `5 passed, 3 xfailed`, so the five pre-existing command tests were unaffected.
- M4 green (2026-09-15 06:15Z): with the markers removed and the module
  implemented, `uv run pytest tests/test_phrases.py -q` reports `14 passed` and
  `uv run pytest tests/test_cli.py -q` reports `8 passed`.
  Non-vacuity (INV-4): replacing mark-then-blank with a sequential
  `pattern.sub` per pattern turns the green run into `1 failed, 13 passed`, and
  the single failure is
  `test_mask_marks_overlapping_spans_against_the_original_text`, which observes
  the phrase surviving inside the second span where the mark-then-blank
  implementation blanks it.
  The single-pattern Hypothesis property still passes under that mutation,
  which is why the explicit overlapping example is kept alongside it. The
  mutation was applied by hand, observed, and reverted; only the test docstring
  describes it.
  Non-vacuity (INV-5): the boundary property records both branches of the
  `boundaries permit a finding` event, and the masking property records both
  branches of `surrounding content is empty`; the masking property also asserts
  that the same text without the ignore pattern does produce a finding.
  `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and
  `uv run interrogate --fail-under 100` over `typos_config_builder tests` all
  pass. `uv run typos --config typos.toml --force-exclude` over the seven
  changed source and documentation files finds nothing.
  `uv run typos-config-builder check-phrases --repository .` exits 0.
  `phrases.py` is 389 lines. The full `make all` gate is run by the lead.
- M4 pylint (2026-09-15 07:05Z): the lead's `make lint-python` run reported
  five `C1803 use-implicit-booleaness-not-comparison` findings in
  `tests/test_phrases.py`, where emptiness was asserted as `== ()` and
  non-emptiness as `!= ()`. The assertions now use truthiness and carry short
  failure messages, which restores the diagnostic the comparison used to give.
  The PyPy-backed runner then reports `10.00/10` over
  `typos_config_builder tests`, and `tests/test_phrases.py` and
  `tests/test_cli.py` still report `14 passed` and `8 passed`.
- M5 red (2026-09-15 08:45Z): with `typos_config_builder/gate.py` present only
  as signatures raising `NotImplementedError` and the new contracts marked
  `xfail(strict=True)`, `uv run pytest tests/test_gate.py -q` reports
  `14 xfailed` and `uv run pytest tests/test_cli.py -q` reports
  `8 passed, 2 xfailed`, so the eight pre-existing command tests were
  unaffected.
- M5 green (2026-09-15 09:35Z): with the markers removed and the module
  implemented, `uv run pytest tests/test_gate.py -q` reports `14 passed`,
  `uv run pytest tests/test_cli.py -q` reports `10 passed`, and
  `uv run pytest tests/test_phrases.py -q` still reports `14 passed`.
  Non-vacuity: the ordering test asserts that `typos.toml` existed at the
  moment the injected runner was called, which a gate running Typos before the
  builder cannot satisfy; the phrase-ordering test drives the injected runner
  to exit 2 and still requires the phrase finding, which a short-circuiting
  gate cannot satisfy. Two `slow`-marked tests run the real pinned binary:
  a repository whose Markdown carries a plain-British form of an Oxford stem
  reports `typos_exit == 2`, and the Oxford form reports 0 and a clean gate.
  `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and
  `uv run interrogate --fail-under 100` over `typos_config_builder tests` all
  pass, and `markdownlint-cli2` over the three changed documents reports
  `0 error(s)`. `gate.py` is 341 lines and `cli.py` is 204 lines.
  `uv run typos-config-builder gate --repository .` reports
  `current: typos.toml` and exits 0. The full `make all` gate is run by the
  lead.

## Idempotence and recovery

All milestones are ordinary commits on branch `audit-missing-functionality`
and can be reverted individually. The bootstrap fallback writes only the
untracked cache files. Consumer PRs are independent and can be closed without
affecting others.

## Artefacts and notes

Baseline gate on `a343fe4` (2026-09-14 18:05Z): `make all` exits 0; 25 tests
pass; `make spelling` reports `refreshed: typos.toml` and Typos finds
nothing.

## Interfaces and dependencies

At the end of M5 the package exposes, in `typos_config_builder/cli.py`:

```python
@app.default
def run(repository: Path | None = None, source: str | None = None, *,
        offline: bool = False, check: bool = False) -> None: ...

@app.command
def check_phrases(repository: Path | None = None) -> None: ...

@app.command
def gate(repository: Path | None = None, source: str | None = None, *,
         offline: bool = False, scope: Scope = "markdown") -> None: ...
```

with `Scope = typing.Literal["markdown", "all"]`. The module behind the
command exposes:

```python
@dc.dataclass(frozen=True, slots=True)
class GateOptions:
    source: str | None = None
    offline: bool = False
    scope: Scope = "markdown"

def gate(repository: Path, options: GateOptions | None = None, *,
         runner: TyposRunner = subprocess.run) -> GateResult: ...
```

and in
`typos_config_builder/builder.py`:

```python
DEFAULT_SOURCE = "https://raw.githubusercontent.com/leynos/agent-helper-scripts/refs/heads/main/data/typos-oxendict-base.toml"
```

Runtime dependencies: `cyclopts`, `pathspec`, `typos`.

## Revision notes

- 2026-09-14 23:45Z: EP-M1 implemented. `builder.DEFAULT_SOURCE` now selects
  the live shared dictionary and `builder.bundled_authority()` is public.
  `cache.RefreshOptions` gained `bootstrap`, and `cache.bootstrap_cache`
  writes the snapshot with `{"source", "sha256", "bootstrap"}` metadata.
  `http.py` calls it from `_stale_cache_or_raise` and from the offline path,
  logging the `bootstrap` decision at WARNING. The bootstrap is configured by
  `build` only when the caller selects no source, so an explicitly chosen
  authority still fails loudly; this keeps the existing offline
  `FileNotFoundError` contract intact. Documentation, the ADR amendment, and
  this repository's `spelling` target were updated, and `typos.local.toml`
  gained the inline-code ignore pattern noted under Surprises.
- 2026-09-14 23:58Z: `bootstrap_cache` took five parameters and tripped the
  Ruff argument-count rules. Its cache, metadata, snapshot, and source
  parameters were grouped into a frozen `cache.BootstrapRequest`, built by the
  new `RefreshOptions.bootstrap_request` method, which also carries the
  "no snapshot configured" decision. The function now takes a request, a
  validator, and a writer, and always returns a result. `http.py` fell to 397
  lines. Evidence: `uv run ruff check`, `uv run ruff format --check`, and
  `uv run interrogate --fail-under 100` over `typos_config_builder tests` all
  pass, and `uv run pytest tests -q` reports `34 passed`.
- 2026-09-15 00:10Z: `ty` rejected the seam test's `options: object`
  annotation. The fake `cache.refresh` in
  `tests/test_build.py::test_default_source_is_live_authority` now annotates
  its parameters with the real seam types, `str | Path`, `Path`,
  `ContentValidator`, and `RefreshOptions`. Evidence:
  `uv run ty check typos_config_builder tests` reports `All checks passed!`,
  with Ruff check, Ruff format check, and `uv run pytest tests -q`
  (`34 passed`) still green.
- 2026-09-15 03:05Z: EP-M3 implemented. The HTTPS refresh path moved out of
  `http.py` into the new `typos_config_builder/remote.py`, which owns transport
  safety, conditional requests, the bounded response read, the stale-cache and
  bootstrap fallbacks, the cache-identity checks, and the bounded refresh
  diagnostics. `http.py` keeps the local and offline paths and the `refresh`
  entry point, imports `remote`, and re-exports `HTTP_NOT_MODIFIED`,
  `TRANSIENT_HTTP_STATUSES`, and `MAX_AUTHORITY_BYTES` so callers keep one
  import site. The offline branch of `refresh` became `_refresh_offline` and
  `_offline_source_name` during the move. `TRANSIENT_HTTP_STATUSES` gained 429.
  `remote._bounded_body` reads at most `MAX_AUTHORITY_BYTES + 1` bytes and
  raises `ValueError` before validation when the body is larger, leaving the
  cache and its metadata untouched; `cache.RemoteResponse.read` gained an
  optional positional size to describe that call. `policy.Dictionary` gained
  `removed_patterns`, `_from_text` parses and compiles `[patterns] remove`, and
  `policy._merge_ignore_patterns` subtracts overlay withdrawals from the union
  and rejects an overlay that both ignores and removes a pattern.
  `removed_patterns` is never rendered. The users' guide documents the
  withdrawal key, the 429 behaviour, and the size cap; the developers' guide
  records the one-way `http` to `remote` import direction.
- 2026-09-15 06:20Z: EP-M4 implemented. `typos_config_builder/phrases.py`
  provides `PhraseFinding`, `PhrasePolicy`, `PhraseScanError`, `load_policy`,
  `tracked_files`, `mask`, `scan_text`, and `find_phrases`. `load_policy` reuses
  `builder.CACHE_NAME`, `OVERLAY_NAME`, `METADATA_NAME`, and `OUTPUT_NAME`, and
  names the builder invocation when the cache is absent. `tracked_files`
  resolves `git` through `shutil.which` and runs `ls-files -z` with
  `stdin=DEVNULL` and `check=True`. Masking marks every span of every compiled
  ignore expression against the original text and blanks the marked characters
  in one pass, preserving newlines. `find_phrases` skips `POLICY_PATHS`, paths
  matched by `pathspec.GitIgnoreSpec`, and tracked symlinks, and wraps `OSError`
  and `UnicodeDecodeError` in `PhraseScanError` with the cause chained.
  `cli.py` gained the `check-phrases` command, which exits 2 on findings and 1
  with a single `error:` line for `OSError`, `ValueError`, `PhraseScanError`,
  and `subprocess.CalledProcessError`. `pathspec>=1.1.1,<2` was added to the
  runtime dependencies and locked at 1.1.1. The `spelling` target now runs
  `check-phrases` after Typos, and this repository's overlay excludes the
  packaged snapshot for the reason recorded under Surprises.
  The plan's Interfaces section already named `check_phrases`; the delivered
  signature matches it.
- 2026-09-15 01:20Z: EP-M2 implemented. `patterns._is_at_most_once` reads a
  quantifier and reports whether it can repeat its atom at most once; `?`,
  `{0,1}` and `{1}` qualify, while `*`, `+`, `{1,}` and `{2,5}` do not.
  `_RepetitionScanner._consume_atom_or_operator` passes
  `repeats_ambiguous_group` only when the quantifier is not at most once, so
  an optional group holding an inner repetition or alternation is accepted
  while still counting as an atom for the adjacent-repetition rule. Lazy `?`
  after `*`, `+`, `}` or `?` is unaffected because `_is_repetition_modifier`
  consumes it first. `hypothesis` was added to the dev dependency group.
  `patterns.py` stands at 218 lines.
- 2026-09-15 09:40Z: EP-M5 implemented. `typos_config_builder/gate.py` provides
  `Scope`, `TyposRunner`, `TyposUnavailableError`, `GateOptions`, `GateResult`,
  `select_files`, `typos_executable`, `run_typos`, and `gate`. `typos_executable`
  prefers the console script installed beside `sys.executable` so the pinned
  1.48.0 wins over an unrelated binary earlier on `PATH`, and raises
  `TyposUnavailableError` naming the interpreter when neither location has it.
  `run_typos` submits at most 500 paths per invocation with `--config
  typos.toml --force-exclude`, adds `--hidden` for the `all` scope, runs with
  `cwd` set to the repository, `check=False`, and `stdin=DEVNULL`, and returns
  the worst exit code; an empty selection returns 0 without starting a process.
  `gate` builds in write mode, lists tracked files, runs Typos over the
  selected scope, and then always runs the phrase check, so a Typos finding
  never suppresses a phrase finding (INV-7). `cli.py` gained the `gate`
  command and one shared `EXPECTED_FAILURES` tuple with an `_exit_with_error`
  handler, which replaced the three commands' duplicated `except` blocks and
  now also covers `TyposUnavailableError`; `_print_findings` is shared between
  `check-phrases` and `gate`. `typos==1.48.0` was added to the runtime
  dependencies and locked. `[tool.pytest.ini_options] markers` gained `slow`
  for the two tests that run the real binary. This repository's `spelling`
  target is now the single line `$(UV) run typos-config-builder gate
  --repository .`, and the unused `TYPOS_VERSION`, `TYPOS`, and
  `MD_FILES_FIND` variables were removed. The users' guide gained a
  "Run the whole gate" section carrying the one-command contract, the
  `.gitignore` lines, the `--scope` table, and the exit codes; its
  "Deliberate limits" list no longer claims the package never executes Typos,
  since that is now its job.
- 2026-09-15 (EP-M6 documentation half): ADR 0001's amendment section was
  rewritten into one coherent record of the four 2026-09-14 owner decisions
  (live authority with bootstrap fallback, Typos execution and phrase
  enforcement in scope, the remaining exclusions, and the three-item
  consumer footprint), with its Status line changed to "Accepted,
  2026-07-14; amended 2026-09-14". The design document's pipeline diagram
  and Boundaries section now name the `gate` pipeline through Typos and the
  phrase check, a new numbered "Commands" section documents the three
  commands and `--scope`, and one sentence records the sorted-list
  gitignore re-inclusion limitation as an open owner question. The users'
  guide now opens with the pinned `gate` invocation, keeps the existing
  repository-files, overlay, default-command, and `check-phrases` sections
  in the same order as building blocks, rewrites "Check for drift" to warn
  against running `--check` in CI, updates "Deliberate limits" to the
  amended exclusion list, and adds a "Migrating an existing consumer"
  subsection with the six-step recipe. `README.md`'s summary paragraph and
  invocation now describe and use the pinned `gate` form. `pyproject.toml`
  already declared version `0.1.0`, so it was left unchanged. Test porting
  from the forks and the `v0.1.0` tag remain outstanding for the rest of
  EP-M6.
