# typos-config-builder design

Status: Accepted

Scope: The Python 3.14 CLI that produces consumer `typos.toml` files.

Audience: Maintainers and reviewers.

Governing decision: [ADR 0001](adrs/0001-keep-the-builder-focused.md).

## 1. Design goal

Estate repositories need identical handling of the shared en-GB-oxendict
dictionary without carrying copies of the Python implementation. The builder
provides that shared mechanism as a versioned package while leaving spelling
policy ownership and tool execution at their existing boundaries.

## 2. Pipeline

The `gate` command, the package's primary workflow, has one linear
responsibility:

```text
live authority (or bootstrap)
-> untracked cache
-> local overlay (ignore, remove)
-> deterministic typos.toml
-> Typos run
-> phrase check
-> exit status
```

The refresh stage updates a valid local cache only when the authority is newer,
or seeds it from the bundled snapshot when no valid cache exists and the
authority cannot be reached. The merge stage adds non-conflicting
repository-specific policy, including `ignore` additions and `remove`
withdrawals. The render stage produces stable TOML. Typos then runs over the
rendered configuration, and the phrase check enforces the corrections Typos
cannot express; the gate's exit status is the worst of the two stages, so a
Typos finding never suppresses a phrase finding.

The lower-level default command stops after rendering, either writing
`typos.toml` or, with `--check`, comparing the rendering with the tracked
output and reporting drift.

## 3. Commands

The CLI exposes three commands over the same pipeline stages:

- The **default command** refreshes the cache, merges the overlay, and
  renders `typos.toml`, writing it or, with `--check`, reporting drift without
  writing.
- **`check-phrases`** loads the cached and merged policy and scans tracked
  files for `[phrases.corrections]` violations, independent of Typos.
- **`gate`** is the composed workflow above: it runs the default command in
  write mode, then the pinned Typos binary, then `check-phrases`, and exits
  with the worst of the two checking stages' results.

`gate` accepts a `--scope` option selecting which tracked files Typos receives:
`markdown` (the default) restricts Typos to files with the `.md` suffix, and
`all` passes every tracked file, including hidden ones. The phrase check always
scans every tracked file regardless of `--scope`, because phrase corrections
apply to any tracked text.

## 4. Boundaries

The package owns:

- shared-dictionary parsing and validation;
- cache freshness and safe replacement, including the bundled bootstrap
  fallback;
- non-conflicting local-overlay merging, including pattern withdrawal;
- deterministic `typos.toml` rendering;
- drift detection suitable for a consumer quality gate;
- running the pinned Typos binary over the configuration it renders; and
- enforcing the shared phrase corrections that Typos cannot express.

The package does not own:

- estate inventory, crawling, or word harvesting;
- decisions about new shared dictionary entries;
- Typos installation beyond its own pinned dependency, or interpreting its
  diagnostics beyond relaying them;
- Nixie or Merman CLI installation and execution; or
- a general policy, workflow, or external-command framework.

The authoritative shared dictionary is the live copy on the `main` branch of
`leynos/agent-helper-scripts`, which the builder fetches by default. An edit to
that dictionary therefore reaches every consumer on its next run, with no
consumer change and no builder release. Each consumer owns its local overlay
and pins this package; the package in turn pins the Typos binary it runs, so
consumers no longer track a separate Typos version. The `--source` option
permits an explicit alternative authority without turning source discovery into
a builder responsibility.

A snapshot of the shared dictionary remains packaged, but only as a bootstrap
fallback. It seeds the cache when no valid cache exists for the selected
authority and that authority cannot be reached, including a first offline run.
A valid cache is never replaced by the snapshot; an unreachable authority with
a matching cache yields a stale-cache result instead. Bootstrap metadata
records the selected authority and a `bootstrap` marker, so the next successful
refresh replaces the snapshot in the ordinary way.

Phrase corrections remain in the cached policy for consumer-side enforcement.
Typos splits punctuation-separated phrases into individual words, so its
configuration cannot express those corrections faithfully.

Rendering sorts every policy list, which makes a gitignore-style re-inclusion
pattern such as `!README.md` inert against a preceding broader exclusion, both
in `check-phrases` and in the generated `extend-exclude` Typos reads; this is
recorded as an open owner question in the ExecPlan rather than resolved here.

## 5. Compatibility and evolution

Before registry publication, consumers pin a complete Git commit. Afterwards,
they pin an exact package version. Behavioural changes therefore travel through
explicit updates and can be reviewed with regenerated `typos.toml` output.
Additions to the CLI or internal abstractions require a direct connection to
the pipeline above; convenience alone is insufficient.
