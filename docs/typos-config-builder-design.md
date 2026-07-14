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

The CLI has one linear responsibility:

```text
newer shared authority -> untracked cache -> local overlay -> deterministic typos.toml -> drift result
```

The refresh stage updates a valid local cache only when the authority is newer.
The merge stage adds non-conflicting repository-specific policy. The render
stage produces stable TOML, and the check stage compares that rendering with
the tracked output.

## 3. Boundaries

The package owns:

- shared-dictionary parsing and validation;
- cache freshness and safe replacement;
- non-conflicting local-overlay merging;
- deterministic `typos.toml` rendering; and
- drift detection suitable for a consumer quality gate.

The package does not own:

- estate inventory, crawling, or word harvesting;
- decisions about new shared dictionary entries;
- Typos installation, execution, or diagnostic interpretation;
- Nixie or Merman CLI installation and execution; or
- a general policy, workflow, or external-command framework.

The shared dictionary is bundled and versioned with this package. Each consumer
owns its local overlay and pins both this package and its Typos binary. The
`--source` option permits an explicit alternative authority without turning
source discovery into a builder responsibility.

Phrase corrections remain in the cached policy for consumer-side enforcement.
Typos splits punctuation-separated phrases into individual words, so its
configuration cannot express those corrections faithfully.

## 4. Compatibility and evolution

Before registry publication, consumers pin a complete Git commit. Afterwards,
they pin an exact package version. Behavioural changes therefore travel through
explicit updates and can be reviewed with regenerated `typos.toml` output.
Additions to the CLI or internal abstractions require a direct connection to
the pipeline above; convenience alone is insufficient.
