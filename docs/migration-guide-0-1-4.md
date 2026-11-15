# Migration guide for 0.1.4

## Who this affects

Every consumer that pins a revision earlier than `v0.1.4` and runs
`check-phrases` or `gate`. Repositories whose tracked text is regular files
within the repository and below 1 MiB need no change beyond the pin: findings
and exit codes are unchanged. Repositories that track symbolic links, FIFOs,
devices, or Markdown files larger than 1 MiB should confirm how the phrase
check now classifies them, because 0.1.4 stops reading inputs it cannot prove
are regular files inside the worktree.

Repositories that have not yet adopted the `gate` command should follow
[the migration guide for 0.1.0](migration-guide-0-1-0.md) first.

## Steps

1. Bump the pinned revision in the `uvx` invocation to `v0.1.4`:

   ```bash
   uvx --from "git+https://github.com/leynos/typos-config-builder.git@v0.1.4" \
     typos-config-builder gate
   ```

2. Run the `gate` command once and read the `metric` lines it writes to
   standard error. Each line reports a fixed skip or failure reason and a
   count, for example
   `metric phrase_scan_files_skipped_total{reason=oversized}=2`.
3. For each non-zero skip count, decide whether the input mattered. A tracked
   symbolic link or a file over 1 MiB is now skipped rather than read, so a
   phrase the check used to report from it is no longer reported.
4. Replace an input that must stay checked with a regular file below the limit,
   or record the skip as accepted. Tracked symlinks, FIFOs, and devices cannot
   be phrase-checked.

## Behaviour changes

- The phrase check skips a tracked symbolic link, a FIFO, a device, and any
  other non-regular entry instead of opening it. It previously validated some
  of these only after the path had already been opened.
- A regular tracked file larger than 1 MiB is skipped. The old reader consumed
  the whole file, so this bounds the time and memory one input can cost.
- On POSIX, each component of a tracked path is opened relative to a directory
  descriptor without following links, and the opened file is then re-verified
  against a fresh walk from the repository root. A directory renamed out of the
  worktree during the scan fails the check closed instead of being read.
  Windows performs the equivalent containment check over relative handles.
- The phrase query no longer logs. The command layer publishes one debug record
  per outcome and writes counter lines to standard error in the form
  `metric phrase_scan_files_skipped_total{reason=REASON}=COUNT` and
  `metric phrase_scan_files_failed_total{reason=REASON}=1`. Neither channel
  carries a tracked path or file content, so existing log parsers that matched
  a path need updating.
- Failure classifications are fixed: a metadata failure, a read failure, and a
  tracked file replaced by a directory each publish their own reason label. The
  process still exits with code 1 and prints one `error: ...` line.
- A library caller of `phrases.find_phrases` now supplies a
  `TrackedTextSource`. `phrase_source.RepositoryTextSource` wraps a Git
  worktree, while a double can yield the same records in memory.
  `PhraseScanResult.outcomes` carries the domain `SkipReason` values, and
  `phrase_scan_reporting` owns the log and metric labels for them. Importing
  the package still has no observability side effect.

## Rollout status

The change is tracked by
[issue 432](https://github.com/leynos/typos-config-builder/issues/432).
