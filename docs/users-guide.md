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

### Withdraw a shared ignore pattern

A repository that needs stricter checking than the shared policy provides may
list exact shared ignore expressions under `[patterns] remove` in
`typos.local.toml`:

```toml
schema = 1

[patterns]
remove = ['`[^`\n]+`']
```

Generation unions the shared and local `ignore` lists and then subtracts every
entry in `remove`, so a withdrawn pattern never reaches `typos.toml`. Matching
is by exact expression text. Removing a pattern the shared base does not
contain is a harmless no-op, so an overlay does not break when shared policy
retires a pattern. Listing the same expression under both `ignore` and
`remove` is contradictory and is rejected. Withdrawals are policy metadata:
they are never rendered into the generated configuration.

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

A refresh treats HTTP 429 like the temporary server statuses 500, 502, 503,
and 504: the run keeps a valid cache for the selected authority and reports
`stale-cache` rather than failing. A response body larger than ten mebibytes is
rejected with an error before any parsing or validation, and the cache is left
untouched, so a misrouted or hostile response cannot exhaust memory or replace
shared policy.

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

## Check phrase corrections

Typos tokenizes on punctuation, so it can never enforce a hyphenated phrase.
The shared dictionary carries those phrases under `[phrases.corrections]`, and
`check-phrases` applies them to a repository's tracked text:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@FULL_COMMIT_SHA" \
  typos-config-builder check-phrases --repository .
```

The command reads `.typos-oxendict-base.toml`, merges `typos.local.toml` when
it is present, and scans every tracked file except the policy documents
themselves. A finding is printed as a location, the phrase as written, and the
prescribed replacement:

```plaintext
docs/users-guide.md:42:15: some-phrase -> somephrase
```

A phrase matches case-insensitively and only when neither neighbouring
character is a word character or a hyphen, so a longer compound is never
reported. Text matched by a shared or local ignore expression is blanked before
scanning, with every line and column preserved, so a finding's location is the
location in the original file. Overlapping ignored spans are marked against the
original text and blanked together, so masking one span never exposes another.

File exclusions are applied with gitignore semantics over the policy's
normalized order. Generation sorts every policy list, so a re-inclusion such as
`!README.md` is ordered before the `*.md` it was written to qualify and has no
effect. Express exclusions without relying on re-inclusion.

The check fails closed. A tracked file that cannot be read, or that is not
valid UTF-8, is an error rather than a silent skip, because a skipped file is
exactly the one most likely to have drifted. Tracked symlinks are skipped so
the scan cannot follow a link out of the repository.

Exit codes are:

| Exit code | Meaning |
| --- | --- |
| 0 | No prohibited phrase was found. |
| 1 | Policy could not be loaded, or tracked text could not be scanned. |
| 2 | At least one prohibited phrase was found. |

Exit code 1 prints a single `error: ...` line on standard error, never a
traceback. A missing `.typos-oxendict-base.toml` names the builder invocation
that creates it.

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

The builder generates and checks configuration, and enforces the shared
phrase corrections through `check-phrases`. The consumer remains responsible
for invoking its pinned Typos binary after the configuration check. Entries
under `[phrases.corrections]` are never rendered, because Typos tokenizes
hyphenated phrases as separate words and would silently ignore them; they are
applied by `check-phrases` instead.

## Deliberate limits

The package does not:

- discover or crawl the code estate;
- harvest words or infer spelling policy from repository contents;
- execute Typos or interpret its findings;
- check spelling of anything other than the shared phrase corrections;
- install or orchestrate Nixie or Merman CLI;
- provide a general-purpose policy or configuration framework.

These limits keep the package focused on reproducible `typos.toml` generation.
