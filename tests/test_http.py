"""Focused HTTPS cache identity and stale-fallback contracts."""

from __future__ import annotations

import http.client
import typing as typ
import urllib.error
from unittest import mock

import pytest
from conftest import authority_text

from typos_config_builder import cache, policy

SOURCE = "https://example.invalid/authority.toml"

if typ.TYPE_CHECKING:
    import pathlib


def _seed_remote_cache(
    repository: pathlib.Path,
    opener: cache.Opener,
    *,
    saved_digest: str | None = None,
) -> tuple[pathlib.Path, cache.RefreshOptions]:
    """Create a valid remote cache and its source-bound metadata."""
    content = authority_text().encode()
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    cache_path.write_bytes(content)
    cache.write_metadata(
        metadata,
        {
            "source": SOURCE,
            "sha256": cache.digest(content) if saved_digest is None else saved_digest,
        },
    )
    return cache_path, cache.RefreshOptions(metadata=metadata, opener=opener)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_http_status_uses_source_and_digest_bound_stale_cache(
    repository: pathlib.Path,
    status: int,
) -> None:
    """Transient server statuses fall back to an identity-matched cache."""
    failure = urllib.error.HTTPError(
        SOURCE,
        status,
        "transient",
        http.client.HTTPMessage(),
        None,
    )
    opener = mock.Mock(side_effect=failure)
    cache_path, options = _seed_remote_cache(repository, opener)

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "stale-cache"


def test_stale_fallback_rejects_cache_with_mismatched_digest(
    repository: pathlib.Path,
) -> None:
    """Matching source metadata cannot authorize altered cache bytes."""
    failure = urllib.error.HTTPError(
        SOURCE,
        503,
        "transient",
        http.client.HTTPMessage(),
        None,
    )
    opener = mock.Mock(side_effect=failure)
    cache_path, options = _seed_remote_cache(
        repository,
        opener,
        saved_digest="not-the-cache-digest",
    )

    with pytest.raises(cache.NetworkUnavailableError):
        cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)


def test_offline_reuse_rejects_cache_from_another_source(
    repository: pathlib.Path,
) -> None:
    """Offline mode cannot reuse cache metadata bound to another source."""
    cache_path, options = _seed_remote_cache(repository, mock.Mock())
    offline = cache.RefreshOptions(metadata=options.metadata, offline=True)

    with pytest.raises(FileNotFoundError, match="cached shared dictionary"):
        cache.refresh(
            "https://other.example.invalid/authority.toml",
            cache_path,
            policy.validate_bytes,
            offline,
        )


def test_response_body_failure_uses_matching_stale_cache(
    repository: pathlib.Path,
) -> None:
    """Transient response-body failures use an identity-matched cache."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.side_effect = http.client.IncompleteRead(b"partial")
    opener = mock.Mock(return_value=response)
    cache_path, options = _seed_remote_cache(repository, opener)

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "stale-cache"


def test_non_transient_client_status_is_not_masked(
    repository: pathlib.Path,
) -> None:
    """A permanent client status remains an authority error."""
    failure = urllib.error.HTTPError(
        SOURCE,
        404,
        "missing",
        http.client.HTTPMessage(),
        None,
    )
    opener = mock.Mock(side_effect=failure)
    cache_path, options = _seed_remote_cache(repository, opener)

    with pytest.raises(urllib.error.HTTPError) as error:
        cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert error.value is failure
