"""Coordinate the focused cache, overlay, rendering, and drift workflow."""

from __future__ import annotations

import dataclasses as dc
import pathlib

from typos_config_builder import cache, policy, render

CACHE_NAME = ".typos-oxendict-base.toml"
METADATA_NAME = ".typos-oxendict-base.json"
OVERLAY_NAME = "typos.local.toml"
OUTPUT_NAME = "typos.toml"


@dc.dataclass(frozen=True, slots=True)
class BuildResult:
    """Describe cache freshness and generated-config drift.

    Attributes
    ----------
    refresh_status
        Cache-refresh outcome reported by the selected authority.
    output
        Path to the generated configuration.
    is_current
        Whether the generated configuration matches the merged policy.
    """

    refresh_status: str
    output: pathlib.Path
    is_current: bool


class ConfigBuilderError(Exception):
    """Base class for expected config-builder failures."""


class ConfigDriftError(ConfigBuilderError):
    """Report that generated configuration is missing or stale.

    Attributes
    ----------
    output
        Path to the missing or stale generated configuration.
    """

    def __init__(self, output: pathlib.Path) -> None:
        """Record the generated configuration that requires regeneration.

        Parameters
        ----------
        output
            Path to the missing or stale generated configuration.
        """
        self.output = output
        super().__init__(f"generated configuration is stale: {output}")


def _bundled_authority() -> pathlib.Path:
    """Return the installed package's shared dictionary path."""
    return pathlib.Path(__file__).with_name("data") / "typos-oxendict-base.toml"


def _dictionary(repository: pathlib.Path) -> policy.Dictionary:
    """Load the refreshed cache and merge an optional sparse overlay."""
    dictionary = policy.load(repository / CACHE_NAME)
    overlay = repository / OVERLAY_NAME
    if overlay.exists():
        dictionary = policy.merge(dictionary, policy.load(overlay, sparse=True))
    return dictionary


def _output_is_current(output: pathlib.Path, rendered: str) -> bool:
    """Return whether existing generated output is valid UTF-8 and current."""
    try:
        return output.read_text(encoding="utf-8") == rendered
    except FileNotFoundError, UnicodeDecodeError:
        return False


def build(
    repository: pathlib.Path,
    source: str | pathlib.Path | None = None,
    *,
    offline: bool = False,
    check: bool = False,
) -> BuildResult:
    """Refresh policy and atomically generate or drift-check ``typos.toml``.

    Parameters
    ----------
    repository
        Consumer repository containing the overlay and generated configuration.
    source
        Local path or HTTPS authority. The bundled authority is used by default.
    offline
        Require an already-valid local cache when true.
    check
        Report drift without replacing the generated configuration when true.

    Returns
    -------
    BuildResult
        Cache status, output path, and generated-config state.

    Raises
    ------
    ConfigDriftError
        If check mode finds a missing or stale generated configuration.
    FileNotFoundError
        If offline mode has no valid cache or a local source is absent.
    ValueError
        If the authority or overlay is invalid or conflicts with policy.

    Examples
    --------
    >>> result = build(pathlib.Path("."), offline=True)
    >>> result.output.name
    'typos.toml'
    """
    selected_source = _bundled_authority() if source is None else source
    refresh_result = cache.refresh(
        selected_source,
        repository / CACHE_NAME,
        policy.validate_bytes,
        cache.RefreshOptions(
            metadata=repository / METADATA_NAME,
            offline=offline,
        ),
    )
    output = repository / OUTPUT_NAME
    rendered = render.render(_dictionary(repository))
    is_current = _output_is_current(output, rendered)
    if check and not is_current:
        raise ConfigDriftError(output)
    if not is_current:
        cache.atomic_write(output, rendered.encode())
        is_current = True
    return BuildResult(refresh_result.status, output, is_current)
