# Repository layout

This reference identifies the small set of paths that define the builder.

- `typos_config_builder/` contains the Python 3.14 library and Cyclopts CLI.
- `tests/` contains focused unit and command-boundary tests.
- `docs/` contains the user contract, design, decision record, and maintainer
  guidance.
- `pyproject.toml` declares the package, Python requirement, dependencies, and
  console entry point.
- `uv.lock` records the resolved development and build environment.
- `typos.local.toml` contains only this repository's spelling exceptions.
- `typos.toml` is generated and must not be edited by hand.

Consumer repositories do not copy the Python package. They retain their local
overlay, generated output, ignored cache files, and a pinned CLI invocation.
