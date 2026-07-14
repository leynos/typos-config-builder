# Repository layout

This reference identifies the small set of paths that define the builder.

- The Python 3.14 library and Cyclopts CLI live in `typos_config_builder/`.
- Focused unit and command-boundary tests are kept in `tests/`.
- Maintainer guidance, the user contract, design, and decision record belong in
  `docs/`.
- `pyproject.toml` declares the package, Python requirement, dependencies, and
  console entry point.
- `uv.lock` records the resolved development and build environment.
- `typos.local.toml` contains only this repository's spelling exceptions.
- `typos.toml` is generated and must not be edited by hand.

Consumer repositories do not copy the Python package. They retain their local
overlay, generated output, ignored cache files, and a pinned CLI invocation.
