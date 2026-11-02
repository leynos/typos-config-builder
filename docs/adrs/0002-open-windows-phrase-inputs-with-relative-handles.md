# Architectural decision record (ADR) 0002: Windows phrase-file handles

## Status

Accepted; decided 2026-11-02: use isolated handle-relative native opens on
Windows phrase inputs.

## Date

2026-11-02.

## Context and problem statement

The phrase checker must open each tracked input beneath the repository root
without following a link introduced by a changed path component. Checking a
path before `os.open()` and checking its descriptor afterwards leaves a race: a
component can change between those checks, and the open can reach the new
target before the descriptor is examined.

Python 3.14 does not support the `dir_fd` argument to `os.open()` on Windows
[^1]. The POSIX relative-open approach therefore cannot provide the Windows
path walk.

## Decision Drivers

- Reject reparse points before traversing each tracked path component.
- Keep reads contained beneath a held repository root.
- Preserve the phrase checker on Windows without exposing a general filesystem
  API.
- Keep platform-specific native calls isolated and testable with doubles.

## Requirements

### Functional requirements

- Open each component relative to a held parent handle.
- Reject reparse points, non-directory intermediate components, non-regular
  final files, and handles resolving outside the repository.

### Technical requirements

- Use `NtCreateFile` with `OBJECT_ATTRIBUTES.RootDirectory` and
  `FILE_OPEN_REPARSE_POINT` for each single-component open.[^2]
- Keep ctypes structures and native bindings in
  `phrase_file_open_windows_native.py`; keep traversal and containment checks in
  `phrase_file_open_windows.py`.
- Limit native bindings to component opens and the handle metadata, path, and
  ownership operations required by this adapter.

## Options considered

### Path-based open with pre- and post-checks

This is portable and simple, but a component can be replaced after the
pre-check. The post-check detects an escaped descriptor only after the open has
already followed the changed path.

### POSIX-only descriptor-relative open

Directory descriptors with no-follow flags provide the required walk on POSIX.
Python does not expose `os.open(..., dir_fd=...)` on Windows, so making this
the only implementation would disable the phrase checker there.

### Handle-relative Windows open

Open each name relative to a held directory handle with `NtCreateFile` and
`FILE_OPEN_REPARSE_POINT`. The Windows adapter checks every opened handle's
type and resolved path before continuing, then validates the final descriptor.
This keeps traversal decisions in the adapter and confines ctypes to the
native-binding module. This option is selected.

## Decision outcome / proposed direction

The POSIX implementation continues to use directory-relative opens and
no-follow flags. Windows uses an isolated ctypes binding to `NtCreateFile`,
opening one component relative to its held parent with
`FILE_OPEN_REPARSE_POINT`. The adapter rejects reparse points before they can
become the next parent and checks that intermediate directories and the final
regular file remain inside the repository.

## Goals and non-goals

- Goals:
  - Prevent a tracked path component from redirecting phrase scanning through
    a symbolic link or other reparse point.
  - Keep the native Windows boundary small and specific to phrase-file opens.
- Non-goals:
  - Provide a general-purpose Windows filesystem abstraction.
  - Replace the existing POSIX descriptor-relative implementation.

## Migration plan

No consumer migration is required. The phrase reader continues to call the
platform-neutral dispatcher.

## Known risks and limitations

- The native Windows API boundary is low-level and must remain isolated from
  phrase policy and content reading.
- Linux tests use native API doubles. The CI workflow also runs the focused
  integration cases on Windows against regular files, a nested file, a symbolic
  link, and a directory junction; the first result is pending until that
  workflow runs.

## Outstanding decisions

None. The design has no open policy choice; native Windows-host validation is
automated in CI.

[^1]: [Python 3.14 `os.open` documentation][python-os-open] documents that
    Windows does not support `dir_fd` for `os.open()`.
[^2]: [Microsoft `NtCreateFile` documentation][microsoft-ntcreatefile] covers
    root-relative `ObjectName` values and `FILE_OPEN_REPARSE_POINT` behaviour.

[python-os-open]: https://docs.python.org/3.14/library/os.html#os.open
[microsoft-ntcreatefile]: https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile
