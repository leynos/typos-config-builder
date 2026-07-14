# Developer guide

This guide is for maintainers of the focused `typos-config-builder` package. The
[design](typos-config-builder-design.md), [repository layout](repository-layout.md),
and [scope decision](adrs/0001-keep-the-builder-focused.md) define its
boundaries.

## Implementation contract

The package targets Python 3.14 and exposes its CLI through the
`pyproject.toml` console entry point. The single command writes configuration
by default; `--check` selects non-writing drift detection. Use Cyclopts for
command parsing and `pathlib` for filesystem paths. Keep policy parsing, cache
refresh, overlay merging, deterministic rendering, and drift checking free from
repository discovery or external-tool orchestration.

The Python implementation accepted in
[Weaver pull request 190](https://github.com/leynos/weaver/pull/190) is the
minimum quality baseline. Subsequent implementation should preserve its typed
boundaries, deterministic output, validated cache behaviour, bounded error
handling, and focused tests. This reference is a floor for implementation
quality, not permission to copy consumer-specific behaviour into the package.

## Change discipline

New behaviour belongs here only when it is necessary to refresh the shared
dictionary cache, combine it with a local overlay, generate `typos.toml`, or
check drift. Estate inventory, spelling discovery, Typos execution, and other
documentation tooling belong in their respective repositories or consumer
workflows.

Prefer small tests at stable input and output boundaries. Add regression
coverage for changed behaviour, but do not expand the test matrix speculatively
or reproduce broad consumer integration suites.

## Local quality gates

Use the Makefile targets documented in `AGENTS.md`. Gate changes with the
relevant formatting, lint, type, test, spelling, and audit targets before
commit. The package's own spelling configuration is generated in the same way
as a consumer's configuration; do not edit `typos.toml` by hand.
