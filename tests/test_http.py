"""Focused HTTPS cache identity and stale-fallback contracts."""

from __future__ import annotations

import http.client
import logging
import typing as typ
import urllib.error
from unittest import mock

import pytest
from conftest import AUTHORITY_SOURCE, authority_text, fake_response, seed_cache

from typos_config_builder import cache, policy, remote

SOURCE = AUTHORITY_SOURCE
OTHER_SOURCE = "https://other.example.invalid/authority.toml"
ETAG = '"shared-authority-etag"'
UNREACHABLE = "authority is unreachable"

if typ.TYPE_CHECKING:
    import pathlib


def _seed_remote_cache(
    repository: pathlib.Path,
    opener: cache.Opener,
    *,
    saved_digest: str | None = None,
) -> tuple[pathlib.Path, cache.RefreshOptions]:
    """Create a valid remote cache and its source-bound metadata."""
    cache_path, metadata = seed_cache(repository, saved_digest=saved_digest)
    return cache_path, cache.RefreshOptions(metadata=metadata, opener=opener)


def _bundled_snapshot(repository: pathlib.Path) -> pathlib.Path:
    """Write a bootstrap snapshot whose bytes differ from every other fixture."""
    bundle = repository / "bundled-authority.toml"
    bundle.write_text(authority_text(stem="bundled"), encoding="utf-8")
    return bundle


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
            OTHER_SOURCE,
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


def bootstrap_error_classes(caplog: pytest.LogCaptureFixture) -> list[str | None]:
    """Return the error class of every bootstrap decision that was logged."""
    return [
        getattr(record, "error_class", None)
        for record in caplog.records
        if getattr(record, "decision", None) == "bootstrap"
    ]


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

    with caplog.at_level(logging.WARNING, logger="typos_config_builder"):
        result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "bootstrap"
    assert cache_path.read_bytes() == bundle.read_bytes()
    assert cache.read_metadata(metadata) == {
        "source": SOURCE,
        "sha256": cache.digest(bundle.read_bytes()),
        "bootstrap": True,
    }
    assert bootstrap_error_classes(caplog) == ["network-unavailable"]


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
        mock.Mock(return_value=fake_response(remote, etag=ETAG))
        if is_reachable
        else mock.Mock(side_effect=OSError(UNREACHABLE))
    )
    if has_cache:
        cache_path, metadata = seed_cache(repository, etag=ETAG)
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Offline operation without a cache establishes shared policy from the bundle."""
    bundle = _bundled_snapshot(repository)
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, offline=True, bootstrap=bundle)

    with caplog.at_level(logging.WARNING, logger="typos_config_builder"):
        result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "bootstrap"
    assert cache_path.read_bytes() == bundle.read_bytes()
    # Offline operation never attempted the network, so a network failure
    # would misdescribe why the bundled snapshot was used.
    assert bootstrap_error_classes(caplog) == ["offline-no-cache"]


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


MAX_AUTHORITY_BYTES = 10 * 1024 * 1024
RATE_LIMITED = 429


class _SizedResponse:
    """Serve a fixed body while honouring the caller's read limit.

    The fake proves the refresh path bounds its own read: a caller asking for
    ``n`` bytes never receives more than ``n``.
    """

    def __init__(self, body: bytes) -> None:
        """Hold the body this response serves and an empty header mapping."""
        self._body = body
        self.headers: dict[str, str] = {}

    def read(self, amount: int | None = None, /) -> bytes:
        """Return the body, truncated to ``amount`` bytes when one is given."""
        return self._body if amount is None else self._body[:amount]

    def __enter__(self) -> _SizedResponse:
        """Enter the response context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the response context without suppressing exceptions."""


@pytest.mark.parametrize("has_cache", [True, False])
def test_rate_limited_status_uses_stale_cache(
    repository: pathlib.Path,
    *,
    has_cache: bool,
) -> None:
    """HTTP 429 preserves a matching cache and otherwise reports unavailability."""
    failure = urllib.error.HTTPError(
        SOURCE,
        RATE_LIMITED,
        "rate limited",
        http.client.HTTPMessage(),
        None,
    )
    opener = mock.Mock(side_effect=failure)
    if has_cache:
        cache_path, options = _seed_remote_cache(repository, opener)
        result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)
        assert result.status == "stale-cache"
        return
    options = cache.RefreshOptions(metadata=repository / "cache.json", opener=opener)
    with pytest.raises(cache.NetworkUnavailableError):
        cache.refresh(
            SOURCE,
            repository / "cache.toml",
            policy.validate_bytes,
            options,
        )


def test_oversized_authority_is_rejected(repository: pathlib.Path) -> None:
    """A body beyond the cap is refused before validation and leaves no cache."""
    assert remote.MAX_AUTHORITY_BYTES == MAX_AUTHORITY_BYTES
    response = _SizedResponse(bytes(MAX_AUTHORITY_BYTES + 1))
    opener = mock.Mock(return_value=response)
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    options = cache.RefreshOptions(metadata=metadata, opener=opener)

    with pytest.raises(ValueError, match="exceeds"):
        cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert not cache_path.exists()
    assert not metadata.exists()
