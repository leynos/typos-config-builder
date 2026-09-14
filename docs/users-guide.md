# typos-config-builder users' guide

This guide is for repositories that generate a tracked `typos.toml` from the
shared en-GB-oxendict dictionary and a narrow local overlay.

## Pin the invocation

Until a package registry release exists, pin the complete Git commit identifier
at the consumer boundary:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@FULL_COMMIT_SHA" \
  typos-config-builder --check
```

Replace `FULL_COMMIT_SHA` with the selected commit. The exact revision makes
policy changes reviewable. Consumers should not invoke an unpinned branch or
latest revision.

After registry publication, the equivalent form pins the released package
version:

```bash
uvx --from typos-config-builder==X.Y.Z typos-config-builder --check
```

## Repository files

The builder operates on four files in the consumer repository:

- `.typos-oxendict-base.toml` is the untracked local cache of the shared
  dictionary.
- `.typos-oxendict-base.json` is untracked refresh metadata.
- `typos.local.toml` is the tracked repository-specific overlay.
- `typos.toml` is the tracked, deterministic generated output.

The shared dictionary remains authoritative for estate-wide Oxford spellings.
The local overlay is only for repository-specific accepted terms, corrections,
patterns, and file exclusions. It must not weaken or contradict the shared
policy.

## Generate configuration

Run the command without `--check` to refresh the cache, merge the local
overlay, and atomically write `typos.toml`:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@FULL_COMMIT_SHA" \
  typos-config-builder
```

The command uses the current directory as the consumer repository. Pass
`--repository PATH` to select another repository. `--source SOURCE` selects an
explicit alternative authority.

Use `--offline` to prohibit refresh from the configured authority and require a
valid existing cache.

## Shared dictionary authority

The default authority is the live shared dictionary on the `main` branch of
`leynos/agent-helper-scripts`:

```plaintext
https://raw.githubusercontent.com/leynos/agent-helper-scripts/refs/heads/main/data/typos-oxendict-base.toml
```

Because the authority is live, an edit to the shared dictionary reaches every
consumer on its next run. No consumer change, pin bump, or builder release is
required to pick up a new accepted word.

The package still ships a snapshot of the shared dictionary, but only as a
bootstrap fallback. The builder writes that snapshot to the cache when, and
only when, there is no valid cache for the selected authority and the authority
cannot be reached, including a first `--offline` run. The decision is logged at
warning level and the refresh status is reported as `bootstrap`. A valid cache
is never replaced by the snapshot: when the authority is unreachable and the
cache matches the selected source, the run reports `stale-cache` and keeps the
cached policy.

## Check for drift

Pass `--check` in a quality gate. The command refreshes the cache, merges the
local overlay, and compares the deterministic rendering with tracked
`typos.toml`. It exits non-zero on drift without rewriting the tracked file.

Because the authority is live, a tracked `typos.toml` drifts whenever the
shared dictionary changes. A continuous-integration gate should therefore run
the builder in write mode and then run Typos against the regenerated
configuration, rather than failing the build on expected drift.

## Builder workflow

The CLI performs a small, ordered workflow:

1. Refresh the cached shared dictionary only when the configured authority is
   newer than the valid local cache.
2. Load the cached dictionary and merge `typos.local.toml` when the overlay is
   present.
3. Render `typos.toml` deterministically.
4. Atomically write the rendered content, or compare it with the tracked file
   when `--check` is set.

The refresh operation preserves a valid cache when the configured authority is
temporarily unavailable. A run that has no valid cache and cannot reach the
authority falls back to the bundled snapshot, so shared policy can always be
established.

The builder only generates and checks configuration. The consumer remains
responsible for invoking its pinned Typos binary after the configuration check.
Typos tokenizes hyphenated phrases as separate words, so entries under
`[phrases.corrections]` remain shared policy metadata for the consumer's phrase
gate rather than being rendered as ineffective `extend-words` entries.

## Deliberate limits

The package does not:

- discover or crawl the code estate;
- harvest words or infer spelling policy from repository contents;
- execute Typos or interpret its findings;
- install or orchestrate Nixie or Merman CLI;
- provide a general-purpose policy or configuration framework.

These limits keep the package focused on reproducible `typos.toml` generation.
