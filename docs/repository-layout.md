# Repository layout

This reference identifies the small set of paths that define the builder.

- The Python 3.14 library and Cyclopts CLI live in `typos_config_builder/`.
- `typos_config_builder/remote.py` owns the HTTPS refresh path: transport
  safety, conditional requests, the bounded response read, and the stale-cache
  and bootstrap fallbacks. `typos_config_builder/http.py` owns the local and
  offline paths and the `refresh` entry point, and imports `remote` in that one
  direction only.
- `typos_config_builder/phrases.py` enforces the shared phrase corrections
  that Typos cannot express. `typos_config_builder/phrases_files.py` owns
  tracked-file selection for it (the Git listing, the policy-file and escape
  checks, and fail-closed reading) and is the only module that shells out to
  Git.
- `typos_config_builder/gate.py` runs the whole gate: generation, the pinned
  Typos binary, and the phrase check. It is the package's only other module
  that starts a subprocess, and the process it starts is Typos.
- Focused unit and command-boundary tests are kept in `tests/`, one module
  per package module (`test_build.py`, `test_http.py`, `test_remote.py`,
  `test_policy.py`, `test_patterns.py`, `test_phrases.py`,
  `test_phrases_files.py` for tracked-file selection, `test_gate.py`,
  `test_gate_typos.py` for the Typos invocation contracts, `test_cli.py`), plus
  shared fixtures in `conftest.py`.
- Maintainer guidance, the user contract, design, and decision record belong in
  `docs/`.
- `pyproject.toml` declares the package, Python requirement, dependencies, and
  console entry point.
- `uv.lock` records the resolved development and build environment.
- `typos.local.toml` contains only this repository's spelling exceptions.
- `typos.toml` is generated and must not be edited by hand.

Consumer repositories do not copy the Python package. They retain their local
overlay, generated output, ignored cache files, and a pinned CLI invocation.
