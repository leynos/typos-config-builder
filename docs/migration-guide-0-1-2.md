# Migration guide for 0.1.2

## Who this affects

Any repository on 0.1.0 or 0.1.1 whose legacy generated `typos.toml` carried
a `[type.markdown]` table, and any repository that wants an ignore expression
to mask documentation without masking source. Repositories that need neither
require no change: 0.1.2 is additive and generates byte-identical output for
every overlay that does not use the new key.

Repositories that have not yet adopted the `gate` command should follow
[the migration guide for 0.1.0](migration-guide-0-1-0.md) first.

## Steps

1. Bump the pinned version in the `uvx` invocation from `v0.1.1` to `v0.1.2`.
2. If the repository's legacy generated `typos.toml` carried a
   `[type.markdown]` table, list that table's `extend-ignore-re` entries
   under `[patterns] markdown_only` in `typos.local.toml`:

   ```toml
   schema = 1

   [patterns]
   markdown_only = ['`[^`\n]+`', '(?s)```.*?```']
   ```

3. Remove those same expressions from `[patterns] ignore` if the overlay
   also listed them there. Leaving them is harmless, because confinement
   wins, but the duplication is misleading.
4. Regenerate by running the `gate` command, and confirm the regenerated
   `typos.toml` reproduces the expected `[type.markdown]` table.

## Behaviour changes

- `[patterns] markdown_only` confines an ignore expression to Markdown. Each
  listed expression is withheld from the merged `[default] extend-ignore-re`
  set, whether the shared dictionary or the local `ignore` list supplied it,
  and is rendered under `[type.markdown]` with an `extend-glob` of `*.md`
  instead. The table is omitted entirely when the list is empty.
- An expression does not have to exist in the shared dictionary to be
  confined: listing one that is absent simply scopes it to Markdown.
- `[patterns] remove` withdraws a confined expression as well as an ignored
  one, so a repository can drop a Markdown mask the shared dictionary ships.
  Listing the same expression under both `remove` and `markdown_only` in one
  overlay remains contradictory and is rejected.
- Markdown confinement does not affect `check-phrases`. The phrase scan masks
  every tracked file with the merged default ignore set, so a confined
  expression no longer masks text for phrase checking.

## Rollout status

Migration work is tracked in
[the audit-missing-functionality execution plan](execplans/audit-missing-functionality.md).
