# ADR 0001: Keep the builder focused

## Status

Accepted, 2026-07-14.

## Context

Copying the spelling configuration generator between estate repositories causes
implementation drift. Centralizing unrelated rollout, discovery, and
tool-orchestration work in the replacement package would create a different
maintenance problem.

## Decision

Provide a versioned Python 3.14 Cyclopts CLI with a bundled shared Oxford
dictionary. Its single command refreshes the local cache, merges a local
overlay, and deterministically generates `typos.toml`; `--check` reports drift
without writing the output.

Exclude estate crawling and harvesting, Typos execution, Nixie and Merman CLI
orchestration, and general-purpose policy processing.

## Amendment 2026-09-14

The default authority is no longer the packaged dictionary but the live shared
dictionary on the `main` branch of `leynos/agent-helper-scripts`. Bundling made
a one-word dictionary change into a pin bump in every consumer, which did not
happen in practice, so shared policy drifted. The packaged dictionary is
retained as a bootstrap fallback used only when no valid cache exists and the
authority cannot be reached. The focus recorded above is unchanged: the builder
still refreshes, merges, renders, and reports drift, and nothing about estate
crawling or harvesting is admitted by this amendment. A further amendment
covering Typos execution and the phrase gate is recorded separately.

## Consequences

Consumers can pin and upgrade one shared implementation. The package remains
small enough to review as a deterministic transformation, while estate rollout
logic and external tool execution stay with the systems that own them.
