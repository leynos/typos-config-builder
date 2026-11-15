# Repository layout

This reference identifies the small set of paths that define the builder.

- The Python 3.14 library and Cyclopts CLI live in `typos_config_builder/`.
- `typos_config_builder/remote.py` owns the HTTPS refresh path: transport
  safety, conditional requests, the bounded response read, and the stale-cache
  and bootstrap fallbacks. `typos_config_builder/http.py` owns the local and
  offline paths and the `refresh` entry point, and imports `remote` in that one
  direction only.
- `typos_config_builder/phrases.py` enforces the shared phrase corrections
  that Typos cannot express. It owns the policy, the domain skip and failure
  reasons, and the `TrackedTextSource` port, and `find_phrases` queries that
  port without touching the filesystem. `phrase_matching.py` is the pure
  matcher beneath it. `phrase_source.py` is the one adapter that enumerates
  Git-tracked paths and maps each filesystem outcome to a domain reason.
  `phrase_scan.py` classifies and reads one path, and
  `phrase_scan_reporting.py` publishes the fixed, path-free diagnostics and
  metrics at the command boundary. `typos_config_builder/phrases_files.py` owns
  tracked-file selection (the Git listing, policy-file and escape checks) and
  bounded phrase reads; it is the only module that shells out to Git.
  `phrases_files.read_tracked_text` is the sole production caller of the
  `phrase_file_open.py` dispatcher. The dispatcher loads
  `phrase_file_open_windows.py` on Windows, and that adapter uses
  `phrase_file_open_windows_native.py` for native bindings and handle-relative
  no-reparse traversal. `typos_config_builder/phrase_scan_results.py` defines
  the fixed eligibility and read outcomes passed from file selection to phrase
  scanning.
- `typos_config_builder/gate.py` runs the whole gate: generation, the pinned
  Typos binary, and the phrase check. It is the package's only other module
  that starts a subprocess, and the process it starts is Typos.
- Focused unit and command-boundary tests are kept in `tests/`, one module
  per package module (`test_build.py`, `test_http.py`, `test_remote.py`,
  `test_policy.py`, `test_patterns.py`, `test_phrases.py`,
  `test_phrases_files.py` and `test_phrases_file_selection.py` for tracked-file
  selection, `test_phrase_scan_diagnostics.py` for path-free diagnostics,
  `test_phrase_scan_reporting.py` for the label mapping,
  `test_phrase_source.py` for the adapter's category translation,
  `test_phrase_path_properties.py` for generated path-containment cases,
  `test_phrase_file_open_windows.py` and
  `test_phrase_file_open_windows_native.py` for portable handle and API tests,
  and `test_phrase_file_open_windows_integration.py` for native Windows opens
  run in Windows CI; `test_gate.py`, `test_gate_typos.py` for the Typos
  invocation contracts, `test_cli.py`), plus shared fixtures in `conftest.py`.
- Maintainer guidance, the user contract, design, and decision record belong in
  `docs/`.
- `pyproject.toml` declares the package, Python requirement, dependencies, and
  console entry point.
- `uv.lock` records the resolved development and build environment.
- `typos.local.toml` contains only this repository's spelling exceptions.
- `typos.toml` is generated and must not be edited by hand.

Consumer repositories do not copy the Python package. They retain their local
overlay, generated output, ignored cache files, and a pinned CLI invocation.
