# ADR 0001: Keep the builder focused

## Status

Accepted, 2026-07-14; amended 2026-09-14.

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

A sweep of fourteen consumer repositories found fourteen independently
maintained copies of the phrase-check script and twenty-eight repositories
whose pinned Typos version or builder commit had never been bumped since
adoption. The owner reviewed the sweep on 2026-09-14 and made four decisions
that amend the original scope above.

1. **Live authority, bundled fallback.** The default authority is no longer
   the packaged dictionary but the current `main` branch of
   `leynos/agent-helper-scripts`. Bundling made a one-word dictionary change
   into a pin bump in every consumer, which did not happen in practice, so
   shared policy drifted. The packaged dictionary is retained, but only as a
   bootstrap fallback used when no valid cache exists and the authority cannot
   be reached.
2. **Typos execution and phrase enforcement are in scope.** Running the
   pinned Typos binary and enforcing `[phrases.corrections]` are part of
   "applying the shared policy consistently", not general-purpose tool
   orchestration. This supersedes the original exclusion of "Typos execution"
   above: the package now owns running Typos with a pinned version and
   enforcing the phrase corrections Typos cannot express.
3. **Still excluded.** Estate crawling and harvesting, Nixie and Merman CLI
   orchestration, and general-purpose policy frameworks remain out of scope,
   exactly as before.
4. **Consumer footprint.** A consumer owns an optional local overlay, two
   `.gitignore` lines, and one `gate` command. It must never run `--check`
   against a tracked `typos.toml` in continuous integration (CI), because a
   live authority makes the tracked file drift on every shared dictionary edit,
   and a drift check would fail every consumer on every such edit.

The focus recorded above is otherwise unchanged: the builder still refreshes,
merges, renders, and reports drift, and nothing about estate crawling or
harvesting is admitted by this amendment.

## Consequences

Consumers can pin and upgrade one shared implementation instead of copying a
generator and a phrase-check script. Running Typos and enforcing phrases inside
the package removes the pinned-version and vendored-script drift the sweep
identified, at the cost of the package now depending on the `typos`
distribution and executing an external binary, both of which the developer's
guide documents as a bounded, leaf-module concern. The package remains small
enough to review as a deterministic transformation followed by one pinned tool
invocation, while estate rollout logic and unrelated tool orchestration stay
with the systems that own them.
