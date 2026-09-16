# Migration guide for 0.1.0

## Who this affects

Any repository that already runs its own spelling check pipeline: a vendored
copy of the configuration generator, a hand-maintained pinned Typos version, or
a locally written phrase-check script and its tests. This guide describes the
one-time change to adopt `typos-config-builder`'s `gate` command as the single
spelling quality gate.

## Previous consumer workflow

Before this release, a consumer repository typically:

- pinned a `typos-config-builder` commit and ran the default command with
  `--check` to detect configuration drift;
- pinned and ran its own Typos binary separately; and
- carried its own phrase-check script, together with the tests for that
  script, or vendored a full copy of the configuration generator.

Each of those pieces needed independent maintenance, and a 2026-09-14 estate
sweep found fourteen independently maintained copies of the phrase-check script
and twenty-eight repositories whose pinned Typos version or builder commit had
never been bumped since adoption.

## New consumer workflow

A consumer now runs one pinned command, `gate`, which regenerates `typos.toml`
from the live shared dictionary, runs the pinned Typos binary over the selected
tracked files, and enforces the shared phrase corrections Typos cannot express:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.0" \
  typos-config-builder gate
```

The live shared dictionary is the default source. The package still bundles a
snapshot of that dictionary, but only as a bootstrap fallback used when no
valid cache exists and the authority cannot be reached.

## Steps

1. Delete the vendored generator script, the vendored phrase-check script,
   and their tests.
2. Drop the `TYPOS_VERSION`, `PATHSPEC_VERSION`, and builder-commit Makefile
   variables, and any helper targets that existed only to invoke them (for
   example a `spelling-helper-test` target).
3. Replace the spelling Makefile target with a single call to `gate`, pinned
   to `v0.1.0`. Add `--scope all` where the previous script scanned the whole
   tree rather than only tracked Markdown:

   ```bash
   uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.0" \
     typos-config-builder gate --scope all
   ```

4. Add the two `.gitignore` lines for the untracked cache, if they are not
   already present:

   ```gitignore
   .typos-oxendict-base.json
   .typos-oxendict-base.toml
   ```

5. Regenerate `typos.toml` once by running the command above, and review the
   diff.
6. Remove any local overlay entries added only to protect names such as the
   Azure or GitHub-flavoured Markdown (GFM) style-guide product names, once the
   shared dictionary carries them; check the regenerated `typos.toml` for the
   corresponding entries before deleting the overlay lines.

## Behaviour changes

- `typos.toml` is rewritten on every `gate` run and must never be
  `--check`ed in continuous integration (CI): because the authority is live, a
  tracked `typos.toml` drifts whenever shared policy changes, so a drift check
  would fail every consumer on every dictionary edit.
- The phrase gate fails closed: an unreadable or undecodable tracked file is
  an error, not a silent skip. Tracked symlinks, submodule gitlinks, and paths
  that resolve outside the repository through a symlinked parent are skipped
  rather than scanned.
- `[patterns] remove` lets a repository withdraw a specific shared ignore
  expression from its overlay, by exact expression text, without weakening the
  shared policy for anything else.
- Optional regex groups are accepted in ignore expressions.
- HTTP 429 is treated as a transient authority error, the same as the 500,
  502, 503, and 504 statuses: a valid cache is kept and the run reports
  `stale-cache` rather than failing.
- A refresh response body larger than ten mebibytes is rejected before any
  parsing or validation, and the cache is left untouched.

## Rollout status

As of 2026-09-15, no consumer has migrated to the `gate` command. Migration
work is tracked in
[the audit-missing-functionality execution plan](execplans/audit-missing-functionality.md).
