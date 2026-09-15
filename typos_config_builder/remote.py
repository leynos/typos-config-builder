"""Fetch shared spelling policy from an HTTPS authority.

The module owns the remote half of a refresh: transport safety, conditional
requests, the bounded response read, and the stale-cache and bootstrap
fallbacks. It also owns the cache-identity primitives and the bounded refresh
diagnostics, because the local authority path in
:mod:`typos_config_builder.http` reuses them and must not import back into a
module that imports it.

Diagnostics expose only bounded decisions, source kinds, and error classes;
authority URLs and local paths are deliberately excluded from logs.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import http.client
import logging
import pathlib
import typing as typ
import urllib.error
import urllib.parse
import urllib.request

from typos_config_builder import cache as cache_support

ContentValidator = cabc.Callable[[bytes], None]
AtomicWriter = cabc.Callable[[pathlib.Path, bytes], None]
HTTP_NOT_MODIFIED = 304
# 429 joins the server-side failures: a rate-limited authority is a temporary
# condition, so an identity-matched cache still represents shared policy.
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
# The shared dictionary is a few hundred kilobytes. Ten mebibytes leaves ample
# room for growth while bounding the memory a hostile or misrouted response can
# consume before validation.
MAX_AUTHORITY_BYTES = 10 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class RefreshContext:
    """Bind refresh policy to validation and persistence seams."""

    options: cache_support.RefreshOptions
    validate: ContentValidator
    atomic_write: AtomicWriter


@dc.dataclass(frozen=True, slots=True)
class RemoteRequestState:
    """Group one remote authority with its cache and saved validators."""

    source: str
    cache: pathlib.Path
    metadata: pathlib.Path
    saved: cabc.Mapping[str, object]


class _HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the HTTPS transport boundary."""

    # The stdlib override must preserve all six positional parameters.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    @typ.override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: typ.IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Follow a redirect only when its resolved target uses HTTPS."""
        if urllib.parse.urlsplit(newurl).scheme != "https":
            error_message = f"shared dictionary redirect must use HTTPS: {newurl}"
            raise cache_support.InsecureSourceError(error_message)
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )

    # pylint: enable=too-many-arguments,too-many-positional-arguments


_HTTPS_OPENER = urllib.request.build_opener(_HttpsRedirectHandler())


def log_decision(
    decision: str,
    source_kind: str,
    *,
    error_class: str = "none",
    level: int = logging.DEBUG,
) -> None:
    """Emit one bounded refresh decision without source identity."""
    LOGGER.log(
        level,
        "Shared dictionary refresh decision",
        extra={
            "operation": "dictionary-refresh",
            "source_kind": source_kind,
            "error_class": error_class,
            "decision": decision,
        },
    )


def bootstrap_or_none(
    state: RemoteRequestState,
    context: RefreshContext,
    *,
    error_class: str = "network-unavailable",
) -> cache_support.RefreshResult | None:
    """Seed the cache from a configured snapshot, or report none is configured.

    The caller names the class of failure that forced the snapshot, because an
    offline run never attempted the network and a network failure would
    misdescribe it in diagnostics.
    """
    request = context.options.bootstrap_request(state.cache, state.source)
    if request is None:
        return None
    result = cache_support.bootstrap_cache(
        request, context.validate, context.atomic_write
    )
    log_decision(
        "bootstrap",
        "bundled",
        error_class=error_class,
        level=logging.WARNING,
    )
    return result


def cache_matches_saved_digest(
    cache: pathlib.Path,
    saved: cabc.Mapping[str, object],
    validate: ContentValidator,
) -> bool:
    """Report whether valid cache bytes match their saved digest."""
    try:
        content = cache.read_bytes()
        validate(content)
    except OSError, UnicodeDecodeError, TypeError, ValueError:
        return False
    return saved.get("sha256") == cache_support.digest(content)


def cache_matches_saved_identity(
    state: RemoteRequestState,
    validate: ContentValidator,
) -> bool:
    """Report whether cache bytes match their saved source and digest."""
    return state.saved.get("source") == state.source and cache_matches_saved_digest(
        state.cache,
        state.saved,
        validate,
    )


def _conditional_headers(saved: cabc.Mapping[str, object]) -> dict[str, str]:
    """Build HTTP validators for the selected authority."""
    headers: dict[str, str] = {}
    etag = saved.get("etag")
    if isinstance(etag, str):
        headers["If-None-Match"] = etag
    modified = saved.get("last_modified")
    if isinstance(modified, str):
        headers["If-Modified-Since"] = modified
    return headers


def _https_request(
    source: str, headers: cabc.Mapping[str, str]
) -> urllib.request.Request:
    """Create a conditional request after enforcing HTTPS."""
    if urllib.parse.urlsplit(source).scheme != "https":
        message = f"shared dictionary URL must use HTTPS: {source}"
        raise cache_support.InsecureSourceError(message)
    return urllib.request.Request(  # noqa: S310 - HTTPS is validated above.
        source,
        headers=dict(headers),
    )


def _bounded_body(
    state: RemoteRequestState,
    response: cache_support.RemoteResponse,
) -> bytes:
    """Read a response body no larger than the accepted authority size."""
    try:
        # One byte beyond the cap is enough to detect an oversized body without
        # buffering the remainder of the response.
        content = response.read(MAX_AUTHORITY_BYTES + 1)
    except (http.client.HTTPException, OSError) as error:
        message = f"shared dictionary authority is unavailable: {state.source}"
        raise cache_support.NetworkUnavailableError(message) from error
    if len(content) > MAX_AUTHORITY_BYTES:
        message = (
            "shared dictionary response exceeds "
            f"{MAX_AUTHORITY_BYTES} bytes: {state.source}"
        )
        raise ValueError(message)
    return content


def _write_remote_cache(
    state: RemoteRequestState,
    response: cache_support.RemoteResponse,
    context: RefreshContext,
) -> cache_support.RefreshResult:
    """Validate and atomically persist a changed remote authority."""
    content = _bounded_body(state, response)
    context.validate(content)
    context.atomic_write(state.cache, content)
    cache_support.write_metadata(
        state.metadata,
        {
            "source": state.source,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "sha256": cache_support.digest(content),
        },
        context.atomic_write,
    )
    log_decision("refreshed", "https")
    return cache_support.RefreshResult("refreshed", state.cache)


def _remote_response_result(
    state: RemoteRequestState,
    response: cache_support.RemoteResponse,
    context: RefreshContext,
) -> cache_support.RefreshResult:
    """Return the cache result for a successful HTTP response."""
    if cache_matches_saved_identity(
        state, context.validate
    ) and cache_support.remote_is_not_newer(state.saved, response.headers):
        log_decision("current", "https")
        return cache_support.RefreshResult("current", state.cache)
    return _write_remote_cache(state, response, context)


def stale_cache_or_raise(
    state: RemoteRequestState,
    error: cache_support.NetworkUnavailableError,
    context: RefreshContext,
) -> cache_support.RefreshResult:
    """Return a source-scoped stale cache or propagate connectivity loss."""
    if cache_matches_saved_identity(state, context.validate):
        log_decision(
            "stale-cache",
            "https",
            error_class="network-unavailable",
            level=logging.INFO,
        )
        return cache_support.RefreshResult("stale-cache", state.cache)
    log_decision(
        "stale-cache-rejected",
        "https",
        error_class="network-unavailable",
        level=logging.WARNING,
    )
    bootstrapped = bootstrap_or_none(state, context)
    if bootstrapped is None:
        raise error
    return bootstrapped


def _is_current_not_modified_response(
    state: RemoteRequestState,
    error: urllib.error.HTTPError,
    context: RefreshContext,
) -> bool:
    """Return whether HTTP 304 confirms the matching cache is current."""
    return error.code == HTTP_NOT_MODIFIED and cache_matches_saved_identity(
        state,
        context.validate,
    )


def _http_error_result(
    state: RemoteRequestState,
    error: urllib.error.HTTPError,
    context: RefreshContext,
) -> cache_support.RefreshResult:
    """Translate cache-safe HTTP statuses into refresh results."""
    if _is_current_not_modified_response(state, error, context):
        log_decision("not-modified", "https", error_class="http-not-modified")
        return cache_support.RefreshResult("current", state.cache)
    if error.code == HTTP_NOT_MODIFIED:
        log_decision(
            "not-modified-rejected",
            "https",
            error_class="http-not-modified",
            level=logging.WARNING,
        )
    if error.code in TRANSIENT_HTTP_STATUSES:
        message = "shared dictionary authority returned a transient HTTP status"
        unavailable = cache_support.NetworkUnavailableError(message)
        return stale_cache_or_raise(state, unavailable, context)
    raise error


def refresh_https(
    source: str,
    cache: pathlib.Path,
    context: RefreshContext,
) -> cache_support.RefreshResult:
    """Conditionally refresh from HTTPS with source-scoped stale fallback.

    Parameters
    ----------
    source
        HTTPS authority for the shared dictionary.
    cache
        Destination for validated authority bytes.
    context
        Refresh options and the validation and persistence seams.

    Returns
    -------
    cache_support.RefreshResult
        Refresh status and path to the valid local cache.

    Raises
    ------
    cache_support.InsecureSourceError
        If the authority or a redirect does not use HTTPS.
    cache_support.NetworkUnavailableError
        If the authority is unavailable and no cache or snapshot can serve.
    ValueError
        If the response exceeds ``MAX_AUTHORITY_BYTES`` or fails validation.

    Examples
    --------
    >>> refresh_https(source, cache, context)  # doctest: +SKIP
    RefreshResult(status='refreshed', cache=PosixPath('cache.toml'))
    """
    saved = cache_support.read_metadata(context.options.metadata)
    state = RemoteRequestState(source, cache, context.options.metadata, saved)
    if not cache_matches_saved_identity(state, context.validate):
        state = RemoteRequestState(source, cache, context.options.metadata, {})
        log_decision("cache-identity-mismatch", "https")
    request = _https_request(source, _conditional_headers(state.saved))
    open_remote = (
        _HTTPS_OPENER.open if context.options.opener is None else context.options.opener
    )
    try:
        response_context = open_remote(request, timeout=30.0)
    except urllib.error.HTTPError as error:
        return _http_error_result(state, error, context)
    except http.client.HTTPException, OSError:
        message = f"shared dictionary authority is unavailable: {source}"
        unavailable = cache_support.NetworkUnavailableError(message)
        return stale_cache_or_raise(state, unavailable, context)
    with response_context as response:
        try:
            return _remote_response_result(
                state,
                response,
                context,
            )
        except cache_support.NetworkUnavailableError as error:
            return stale_cache_or_raise(state, error, context)
