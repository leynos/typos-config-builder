# Developer guide

This guide explains the contributor workflow for the generated project.

## Local workflow

The public entrypoint for formatting, linting, typechecking, tests, and spelling
is `make all`. Narrower Make targets may be invoked when investigating a
specific failure, and changes should be reconciled with the aggregate gate
before being considered complete.

`make lint` runs Ruff, `interrogate --fail-under 100 $(PYTHON_TARGETS)` for
100% docstring coverage across `$(PYTHON_TARGETS)`, and Pylint.

Run `make audit` as the dependency vulnerability gate. It runs `pip-audit` for
Python dependencies, and Rust-enabled projects also run `cargo audit` from the
`rust_extension` crate directory.

## Automation scripts

The [Scripting standards](scripting-standards.md) document provides guidance for
adding or updating helper scripts. New and updated scripts are expected to use
`Cyclopts` for command-line interfaces, `cuprum` for typed and catalogue-bound
external command execution, `pathlib` for filesystem paths, and `cmd-mox` for
tests that mock external executables.

Script changes should update the scripting guide when they introduce a new
convention, command catalogue, testing pattern, or operational expectation that
future contributors need to follow.

## GitHub Actions

The generated repository includes GitHub Actions workflows and local composite
actions under `.github/`.

- `.github/workflows/ci.yml` runs on pushes to `main` and on pull requests. It
  sets up Python 3.13, installs `uv`, validates the generated `Makefile` with
  `mbake`, runs `make build`, `make check-fmt`,
  `make lint` (Ruff + `interrogate --fail-under 100 $(PYTHON_TARGETS)` + Pylint),
  `make typecheck`, `make spelling`, and `make audit`, then delegates coverage
  generation to the shared coverage action. When the Rust extension is enabled,
  it also sets up Rust, installs Rust lint and test tools, and passes
  `rust_extension/Cargo.toml` to coverage.
- `.github/workflows/act-validation.yml` runs rendered workflow validation in a
  separate workflow. It installs `act`, checks Docker availability, and runs
  `make test WITH_ACT=1` outside the coverage path.
- `.github/workflows/release.yml` publishes wheels when a `v*.*.*` tag is
  pushed. It builds a pure Python wheel, creates a GitHub release with generated
  release notes, downloads wheel artefacts, and uploads them to the tag release.
- `.github/workflows/build-wheels.yml` is a reusable workflow for extension
  builds. It accepts a Python version and builds wheels across Linux, Windows,
  and macOS architectures via `.github/actions/build-wheels`.
- `.github/workflows/get-codescene-sha.yml` is manually dispatched. It fetches
  the CodeScene coverage CLI installer, computes its SHA-256 digest, and writes
  the result to the `CODESCENE_CLI_SHA256` repository variable.
- `.github/actions/build-wheels` wraps `cibuildwheel` with `uvx` and uploads
  architecture-specific wheel artefacts.
- `.github/actions/pure-python-wheel` builds a pure Python wheel with
  `uv build --wheel` and uploads the resulting artefact.
- `.github/dependabot.yml` enables dependency update pull requests for GitHub
  Actions and Python packages. Rust-enabled projects also receive Cargo updates.

The `CS_ACCESS_TOKEN` secret must be configured when CodeScene coverage upload
is required. The `CODESCENE_CLI_SHA256` variable should be populated using the
refresh workflow, so CI can verify the downloaded CodeScene installer before
upload.

## Shared spelling configuration

Run `make spelling` to enforce en-GB-oxendict spelling. The generator fetches
the estate-wide base from `leynos/agent-helper-scripts` only when its authority
is newer than the ignored local cache. A populated cache supports offline
generation. Add only project-specific terms and exclusions to
`typos.local.toml`; never edit generated `typos.toml` by hand.
