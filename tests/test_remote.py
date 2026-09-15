"""Transport-security and conditional-request contracts for HTTPS refresh.

These contracts are ported from the consumer forks of the generator, which
carried the same transport and freshness rules in vendored scripts. They sit
apart from ``test_http.py`` so neither module outgrows the repository's
four-hundred-line limit: this module owns the transport boundary and the
validators sent with a conditional request, while ``test_http.py`` owns cache
identity and the stale-cache and bootstrap fallbacks.
"""

from __future__ import annotations

import http.client
import tomllib
import typing as typ
import urllib.error
import urllib.request
from unittest import mock

import pytest
from conftest import AUTHORITY_SOURCE, authority_text, fake_response, seed_cache

from typos_config_builder import cache, policy, remote

if typ.TYPE_CHECKING:
    import pathlib

SOURCE = AUTHORITY_SOURCE
OTHER_SOURCE = "https://other.example.invalid/authority.toml"
CACHE_DIGEST = cache.digest(authority_text().encode())
MALFORMED_BODY = b"not = [valid"
SAVED_ETAG = '"authority-v1"'
CHANGED_ETAG = '"authority-v2"'
MODIFIED = "Fri, 10 Jul 2026 08:00:00 GMT"
EARLIER = "Fri, 10 Jul 2026 07:00:00 GMT"
UNPARSEABLE = "whenever"


@pytest.mark.parametrize(
    "source",
    ["http://example.invalid/authority.toml", "ftp://example.invalid/authority.toml"],
)
def test_remote_source_must_use_https(
    repository: pathlib.Path,
    source: str,
) -> None:
    """A remote authority cannot bypass the HTTPS transport boundary.

    Ported from the ``weaver`` fork. The opener assertion is the negative
    control: the scheme is refused before any request is attempted.
    """
    opener = mock.Mock()
    options = cache.RefreshOptions(
        metadata=repository / "cache.json",
        opener=opener,
    )

    with pytest.raises(cache.InsecureSourceError, match="URL must use HTTPS"):
        cache.refresh(
            source,
            repository / "cache.toml",
            policy.validate_bytes,
            options,
        )

    assert not opener.called, "an insecure authority was contacted anyway"


def _redirect_to(target: str) -> urllib.request.Request | None:
    """Ask the guarded handler to follow one redirect to ``target``."""
    handler = remote._HttpsRedirectHandler()
    return handler.redirect_request(
        urllib.request.Request(SOURCE),  # noqa: S310 - HTTPS, and never opened.
        typ.cast("typ.IO[bytes]", None),
        302,
        "Found",
        http.client.HTTPMessage(),
        target,
    )


@pytest.mark.parametrize(
    "target",
    ["http://example.invalid/authority.toml", "ftp://example.invalid/authority.toml"],
)
def test_redirect_away_from_https_is_rejected(target: str) -> None:
    """A redirect cannot downgrade or leave HTTPS.

    Ported from the ``weaver`` and ``ortho-config`` forks.
    """
    with pytest.raises(cache.InsecureSourceError, match="redirect must use HTTPS"):
        _redirect_to(target)


def test_redirect_within_https_is_followed() -> None:
    """A redirect that stays on HTTPS is still followed.

    Negative control for the rejection cases: a guard that refused every
    redirect would satisfy them without preserving ordinary behaviour.
    """
    request = _redirect_to(OTHER_SOURCE)

    assert request is not None
    assert request.full_url == OTHER_SOURCE


def _not_modified() -> urllib.error.HTTPError:
    """Build the production representation of an HTTP 304 response."""
    return urllib.error.HTTPError(
        SOURCE,
        304,
        "not modified",
        http.client.HTTPMessage(),
        None,
    )


def test_not_modified_confirms_a_digest_bound_cache(
    repository: pathlib.Path,
) -> None:
    """HTTP 304 reports a source-matched and digest-matched cache as current.

    Ported from the ``ortho-config`` fork. This is the non-vacuous control
    for the rejection cases below: a refresh that never accepted a 304 would
    satisfy them all.
    """
    cache_path, metadata = seed_cache(repository)
    options = cache.RefreshOptions(
        metadata=metadata,
        opener=mock.Mock(side_effect=_not_modified()),
    )

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == "current"
    assert result.cache == cache_path


@pytest.mark.parametrize(
    ("saved", "has_cache"),
    [
        pytest.param(
            {"source": SOURCE, "sha256": "not-the-cache-digest"},
            True,
            id="digest-mismatch",
        ),
        pytest.param(
            {"source": OTHER_SOURCE, "sha256": CACHE_DIGEST},
            True,
            id="another-source",
        ),
        pytest.param({"sha256": CACHE_DIGEST}, True, id="unscoped-metadata"),
        pytest.param({"source": SOURCE, "sha256": CACHE_DIGEST}, False, id="no-cache"),
    ],
)
def test_not_modified_rejects_a_cache_it_cannot_identify(
    repository: pathlib.Path,
    saved: dict[str, object],
    *,
    has_cache: bool,
) -> None:
    """HTTP 304 cannot validate a cache outside its source and digest.

    Ported from the ``ortho-config`` fork, with the digest row the builder's
    identity binding adds.
    """
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    if has_cache:
        cache_path.write_bytes(authority_text().encode())
    cache.write_metadata(metadata, saved)
    error = _not_modified()
    options = cache.RefreshOptions(
        metadata=metadata,
        opener=mock.Mock(side_effect=error),
    )

    with pytest.raises(urllib.error.HTTPError) as raised:
        cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert raised.value is error


def test_invalid_authority_body_leaves_a_valid_cache_intact(
    repository: pathlib.Path,
) -> None:
    """Malformed authority bytes are rejected before they reach the cache.

    Ported from the ``weaver`` fork, which asserted only that no cache was
    created. The builder must also preserve the cache it already holds,
    because that cache still represents shared policy.
    """
    cache_path, metadata = seed_cache(repository)
    options = cache.RefreshOptions(
        metadata=metadata,
        opener=mock.Mock(return_value=fake_response(MALFORMED_BODY)),
    )

    with pytest.raises(tomllib.TOMLDecodeError):
        cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert cache_path.read_bytes() == authority_text().encode()
    assert cache.read_metadata(metadata)["sha256"] == CACHE_DIGEST


@pytest.mark.parametrize(
    ("response_etag", "expected", "expected_stem"),
    [
        pytest.param(SAVED_ETAG, "current", "organ", id="unchanged-etag"),
        pytest.param(CHANGED_ETAG, "refreshed", "replacement", id="changed-etag"),
    ],
)
def test_etag_takes_precedence_over_an_unchanged_date(
    repository: pathlib.Path,
    response_etag: str,
    expected: str,
    expected_stem: str,
) -> None:
    """A changed ETag refreshes even when Last-Modified is unchanged.

    Ported from the ``weaver`` and ``ortho-config`` forks. The unchanged-ETag
    row is the control: it holds the date validator constant, so only the
    ETag can explain the difference in outcome.
    """
    cache_path, metadata = seed_cache(
        repository,
        etag=SAVED_ETAG,
        last_modified=MODIFIED,
    )
    replacement = authority_text(stem="replacement").encode()
    options = cache.RefreshOptions(
        metadata=metadata,
        opener=mock.Mock(
            return_value=fake_response(
                replacement,
                etag=response_etag,
                last_modified=MODIFIED,
            )
        ),
    )

    result = cache.refresh(SOURCE, cache_path, policy.validate_bytes, options)

    assert result.status == expected
    assert policy.load(cache_path).stems == (expected_stem,)


@pytest.mark.parametrize(
    ("saved", "headers", "expected"),
    [
        pytest.param(
            {"last_modified": MODIFIED},
            {"Last-Modified": EARLIER},
            True,
            id="older-remote-date",
        ),
        pytest.param(
            {"last_modified": EARLIER},
            {"Last-Modified": MODIFIED},
            False,
            id="newer-remote-date",
        ),
        pytest.param(
            {"last_modified": UNPARSEABLE},
            {"Last-Modified": UNPARSEABLE},
            True,
            id="matching-unparseable-dates",
        ),
        pytest.param(
            {"last_modified": UNPARSEABLE},
            {"Last-Modified": "some day"},
            False,
            id="differing-unparseable-dates",
        ),
        pytest.param({}, {"Last-Modified": UNPARSEABLE}, False, id="no-saved-date"),
    ],
)
def test_unparseable_dates_fall_back_to_conservative_equality(
    saved: dict[str, object],
    headers: dict[str, str],
    *,
    expected: bool,
) -> None:
    """A date that cannot be parsed proves freshness only when identical.

    Ported from the ``weaver`` fork. The two parseable rows are the control:
    they show the comparison is ordinarily by date, not by equality.
    """
    assert cache.remote_is_not_newer(saved, headers) is expected
