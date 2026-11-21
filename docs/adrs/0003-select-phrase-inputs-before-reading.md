# Architectural decision record (ADR) 0003: Select phrase inputs before reading

## Status

Accepted; decided 2026-11-16: apply the phrase policy to a tracked path before
the source is asked for its text.

## Date

2026-11-16.

## Context and problem statement

The phrase scan reads a repository's tracked text and reports prohibited
phrases. Some tracked paths are never scanned: the policy documents that define
the rules, and any path the policy excludes. The order of two steps decides
what a failure means. If the policy is applied to the path first, an excluded
path or a policy document is never opened, so nothing about its contents or its
readability can affect the scan. If the source reads first and the query
filters afterwards, a policy document replaced by a directory, or an excluded
path that has become unreadable, aborts the whole scan.

An earlier refactor of the phrase scan introduced a port between the domain
query and the filesystem. Its first shape had the source yield one fully read
record per tracked path, and the query filtered those records afterwards. That
shape silently moved selection after the read and turned an unreadable excluded
path into a fail-closed `PhraseScanError`, which the scan had not done before.

## Decision Drivers

- A policy exclusion states that a path is out of scope for the scan.
- A scan must not fail on an input its own policy removed from scope.
- The domain owns the policy; the adapter owns the filesystem.
- A regression must be visible to a test rather than only to review.

## Requirements

### Functional requirements

- Never open a policy document or an excluded path during a phrase scan.
- Never report a skip outcome for an unselected path.
- Keep failing closed for a selected path that cannot be read.

### Technical requirements

- Give the port separate enumeration and read operations, so the query can
  decide between them.
- Apply `POLICY_PATHS` and the exclusion spec to the path alone.
- Cover both boundaries: an in-memory source proving no read is issued, and a
  worktree fixture proving an unreadable unselected path leaves the scan intact.

## Options considered

### One-method port yielding read records

The source reads every tracked path and the query filters the resulting records
by path. Selection is expressible but not enforceable: the query cannot prevent
a read it receives the result of. This is the shape that introduced the defect.

### One-method port yielding paths, with the adapter filtering

The source receives the exclusions and policy paths, and skips them before
reading. Selection then happens in the adapter, so the adapter must construct
and own policy documents and the domain no longer decides what its own policy
means.

### Two-method port: enumerate paths, then read a selected path

`tracked_paths()` supplies the enumeration and `read(relative)` supplies one
record. The query applies its policy between the two calls, so the domain keeps
the policy and the adapter keeps the filesystem. This option is selected.

## Decision outcome / proposed direction

`phrases.TrackedTextSource` declares `tracked_paths` and `read`.
`phrases.find_phrases` enumerates the paths, drops policy documents and
excluded paths, and calls `read` only for what remains.
`phrase_source.RepositoryTextSource` implements both operations over Git
enumeration and the bounded reader, and translates a read failure into
`phrases.PhraseScanError`. Enumeration failures keep their own errors:
`subprocess.CalledProcessError` when Git cannot list the tracked files, and
`FileNotFoundError` when `git` is not on the executable search path.

## Goals and non-goals

- Goals:
  - Make the exclusion check precede every read.
  - Keep policy knowledge in the domain and filesystem knowledge in the
    adapter.
  - Make the ordering testable without patching an implementation.
- Non-goals:
  - Change which paths are excluded, or how exclusion patterns are matched.
  - Change the diagnostics or metrics a scan publishes.

## Migration plan

No consumer migration is required. The repository has not released this port;
`docs/migration-guide-0-1-4.md` describes the released shape.

## Known risks and limitations

- A source that reads during `tracked_paths()` would defeat the ordering. The
  adapter documents enumeration as metadata-only, and the in-memory regression
  test fails if the query reads an unselected path.
- The port now has two methods, so a double must supply both. The alternative
  was a predicate parameter that let an adapter ignore the policy silently.

## Outstanding decisions

None. The boundary and its test coverage are fixed by this record.
`tests/test_phrase_policy_selection.py` fails on the one-method shape and
passes on this one.
