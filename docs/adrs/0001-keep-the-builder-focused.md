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

## Consequences

Consumers can pin and upgrade one shared implementation. The package remains
small enough to review as a deterministic transformation, while estate rollout
logic and external tool execution stay with the systems that own them.
