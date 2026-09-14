"""Focused HTTPS cache identity and stale-fallback contracts."""

from __future__ import annotations

import http.client
import logging
import typing as typ
import urllib.error
from unittest import mock

import pytest
from conftest import authority_text

from typos_config_builder import cache, policy

SOURCE = "https://example.invalid/authority.toml"
ETAG = '"shared-authority-etag"'
UNREACHABLE = "authority is unreachable"

if typ.TYPE_CHECKING:
    import pathlib


def _seed_cache(
    repository: pathlib.Path,
    *,
    saved_digest: str | None = None,
    etag: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a valid cache and its source-bound metadata sidecar."""
    content = authority_text().encode()
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    cache_path.write_bytes(content)
    saved: dict[str, object] = {
        "source": SOURCE,
        "sha256": cache.digest(content) if saved_digest is None else saved_digest,
    }
    if etag is not None:
        saved["etag"] = etag
    cache.write_metadata(metadata, saved)
    return cache_path, metadata


def _seed_remote_cache(
    repository: pathlib.Path,
    opener: cache.Opener,
    *,
    saved_digest: str | None = None,
) -> tuple[pathlib.Path, cache.RefreshOptions]:
    """Create a valid remote cache and its source-bound metadata."""
    cache_path, metadata = _seed_cache(repository, saved_digest=saved_digest)
    return cache_path, cache.RefreshOptions(metadata=metadata, opener=opener)


def _bundled_snapshot(repository: pathlib.Path) -> pathlib.Path:
    """Write a bootstrap snapshot whose bytes differ from every other fixture."""
    bundle = repository / "bundled-authority.toml"
    bundle.write_text(authority_text(stem="bundled"), encoding="utf-8")
    return bundle


def _response(content: bytes, *, etag: str | None = None) -> mock.MagicMock:
    """Return a context-manager response with a fixed body and headers."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = content
    response.headers = {} if etag is None else {"ETag": etag}
    return response


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


def test_unreachable_authority_without_cache_bootstraps_from_bundle(
    repository: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreachable authority without a cache seeds the bundled snapshot."""
    bundle = _bundled_snapshot(repository)
    opener = mock.Mock(side_effect=OSError(UNREACHABLE))
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, opener=opener, bootstrap=bundle)

    with caplog.at_level(logging.WARNING, logger="typos_config_builder.http"):
        result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "bootstrap"
    assert cache_path.read_bytes() == bundle.read_bytes()
    assert cache.read_metadata(metadata) == {
        "source": SOURCE,
        "sha256": cache.digest(bundle.read_bytes()),
        "bootstrap": True,
    }
    decisions = [
        getattr(record, "decision", None)
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert "bootstrap" in decisions


@pytest.mark.parametrize(
    ("has_cache", "is_reachable", "expected"),
    [
        (True, True, "current"),
        (True, False, "stale-cache"),
        (False, True, "refreshed"),
        (False, False, "bootstrap"),
    ],
)
def test_bootstrap_is_used_only_without_a_source_matching_cache(
    repository: pathlib.Path,
    *,
    has_cache: bool,
    is_reachable: bool,
    expected: str,
) -> None:
    """The bundled snapshot seeds a cache only when no valid cache can serve."""
    bundle = _bundled_snapshot(repository)
    remote = authority_text(stem="remote").encode()
    opener = (
        mock.Mock(return_value=_response(remote, etag=ETAG))
        if is_reachable
        else mock.Mock(side_effect=OSError(UNREACHABLE))
    )
    if has_cache:
        cache_path, metadata = _seed_cache(repository, etag=ETAG)
    else:
        cache_path = repository / "cache.toml"
        metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, opener=opener, bootstrap=bundle)

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == expected
    is_bootstrapped = cache_path.read_bytes() == bundle.read_bytes()
    assert is_bootstrapped == (expected == "bootstrap")
    if has_cache:
        assert cache_path.read_bytes() == authority_text().encode()


def test_offline_without_cache_bootstraps_from_bundle(
    repository: pathlib.Path,
) -> None:
    """Offline operation without a cache establishes shared policy from the bundle."""
    bundle = _bundled_snapshot(repository)
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, offline=True, bootstrap=bundle)

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "bootstrap"
    assert cache_path.read_bytes() == bundle.read_bytes()


def test_offline_with_bootstrapped_cache_reuses_it(repository: pathlib.Path) -> None:
    """A bootstrapped cache satisfies a later offline run without rewriting."""
    bundle = _bundled_snapshot(repository)
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, offline=True, bootstrap=bundle)
    cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "offline-cache"


def test_unreachable_authority_without_bootstrap_still_fails(
    repository: pathlib.Path,
) -> None:
    """Without a configured bundle an unreachable authority remains an error."""
    opener = mock.Mock(side_effect=OSError(UNREACHABLE))
    options = cache.RefreshOptions(metadata=repository / "cache.json", opener=opener)

    with pytest.raises(cache.NetworkUnavailableError):
        cache.refresh(
            SOURCE,
            repository / "cache.toml",
            policy.validate_bytes,
            options,
        )
