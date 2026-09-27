# AGENTS.md spelling block

Every consumer repository documents its spelling gate for coding agents with
the block below, copied verbatim into its `AGENTS.md`, markers included. The
block replaces any other spelling or Typos prose there. Where `AGENTS.md` has
no natural home for it, add it after the Markdown guidance.

The block is versioned with the builder: a consumer copies the text from the
release it pins, and a check that compares the two reads the text between the
markers with whitespace normalized. Do not edit the copied block in place;
change it here and let consumers take the new text with their next pin bump.

<!-- typos-config-builder:agents-md:start -->

## Spelling

- `make spelling` runs the pinned `typos-config-builder gate`, which
  regenerates `typos.toml` from the shared en-GB-oxendict dictionary and
  `typos.local.toml`, then checks spelling and the shared phrase corrections.
- `typos.toml` is generated: never edit it by hand. Put narrow
  repository-specific exceptions in `typos.local.toml`, as exact or full-line
  patterns rather than bare accepted words.
- When `make spelling` changes `typos.toml`, commit the regenerated file. If
  the change is unrelated to your work, commit it in a separate base pull
  request and stack your branch on it, so each review diff stays focused.

<!-- typos-config-builder:agents-md:end -->
