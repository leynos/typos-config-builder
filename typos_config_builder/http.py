"""Refresh spelling policy from source-scoped local or HTTPS authorities.

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
TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
LOGGER = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class _RefreshContext:
    """Bind refresh policy to validation and persistence seams."""

    options: cache_support.RefreshOptions
    validate: ContentValidator
    atomic_write: AtomicWriter


@dc.dataclass(frozen=True, slots=True)
class _LocalSourceState:
    """Group local authority identity and freshness state."""

    name: str
    mtime_ns: int


@dc.dataclass(frozen=True, slots=True)
class _RemoteRequestState:
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


def _log_decision(
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


def _local_cache_is_current(
    cache: pathlib.Path,
    saved: cabc.Mapping[str, object],
    source: _LocalSourceState,
    validate: ContentValidator,
) -> bool:
    """Report whether source-scoped metadata proves a local cache current."""
    saved_mtime = saved.get("mtime_ns")
    return (
        saved.get("source") == source.name
        and isinstance(saved_mtime, int)
        and source.mtime_ns <= saved_mtime
        and _cache_matches_saved_digest(cache, saved, validate)
    )


def _refresh_local(
    source: pathlib.Path,
    cache: pathlib.Path,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Refresh from a local authority only when it is newer."""
    source_stat = source.stat()
    source_state = _LocalSourceState(
        name=str(source.resolve()),
        mtime_ns=source_stat.st_mtime_ns,
    )
    saved = cache_support.read_metadata(context.options.metadata)
    if _local_cache_is_current(cache, saved, source_state, context.validate):
        _log_decision("current", "local")
        return cache_support.RefreshResult("current", cache)
    decision = (
        "source-mismatch" if saved.get("source") != source_state.name else "newer"
    )
    _log_decision(decision, "local")
    content = source.read_bytes()
    context.validate(content)
    context.atomic_write(cache, content)
    cache_support.write_metadata(
        context.options.metadata,
        {
            "source": source_state.name,
            "mtime_ns": source_state.mtime_ns,
            "sha256": cache_support.digest(content),
        },
        context.atomic_write,
    )
    return cache_support.RefreshResult("refreshed", cache)


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


def _cache_matches_saved_digest(
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


def _cache_matches_saved_identity(
    state: _RemoteRequestState,
    validate: ContentValidator,
) -> bool:
    """Report whether cache bytes match their saved source and digest."""
    return state.saved.get("source") == state.source and _cache_matches_saved_digest(
        state.cache,
        state.saved,
        validate,
    )


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


def _write_remote_cache(
    state: _RemoteRequestState,
    response: cache_support.RemoteResponse,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Validate and atomically persist a changed remote authority."""
    try:
        content = response.read()
    except (http.client.HTTPException, OSError) as error:
        message = f"shared dictionary authority is unavailable: {state.source}"
        raise cache_support.NetworkUnavailableError(message) from error
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
    _log_decision("refreshed", "https")
    return cache_support.RefreshResult("refreshed", state.cache)


def _remote_response_result(
    state: _RemoteRequestState,
    response: cache_support.RemoteResponse,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Return the cache result for a successful HTTP response."""
    if _cache_matches_saved_identity(
        state, context.validate
    ) and cache_support.remote_is_not_newer(state.saved, response.headers):
        _log_decision("current", "https")
        return cache_support.RefreshResult("current", state.cache)
    return _write_remote_cache(state, response, context)


def _stale_cache_or_raise(
    state: _RemoteRequestState,
    error: cache_support.NetworkUnavailableError,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Return a source-scoped stale cache or propagate connectivity loss."""
    if _cache_matches_saved_identity(state, context.validate):
        _log_decision(
            "stale-cache",
            "https",
            error_class="network-unavailable",
            level=logging.INFO,
        )
        return cache_support.RefreshResult("stale-cache", state.cache)
    _log_decision(
        "stale-cache-rejected",
        "https",
        error_class="network-unavailable",
        level=logging.WARNING,
    )
    raise error


def _is_current_not_modified_response(
    state: _RemoteRequestState,
    error: urllib.error.HTTPError,
    context: _RefreshContext,
) -> bool:
    """Return whether HTTP 304 confirms the matching cache is current."""
    return error.code == HTTP_NOT_MODIFIED and _cache_matches_saved_identity(
        state,
        context.validate,
    )


def _http_error_result(
    state: _RemoteRequestState,
    error: urllib.error.HTTPError,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Translate cache-safe HTTP statuses into refresh results."""
    if _is_current_not_modified_response(state, error, context):
        _log_decision("not-modified", "https", error_class="http-not-modified")
        return cache_support.RefreshResult("current", state.cache)
    if error.code == HTTP_NOT_MODIFIED:
        _log_decision(
            "not-modified-rejected",
            "https",
            error_class="http-not-modified",
            level=logging.WARNING,
        )
    if error.code in TRANSIENT_HTTP_STATUSES:
        message = "shared dictionary authority returned a transient HTTP status"
        unavailable = cache_support.NetworkUnavailableError(message)
        return _stale_cache_or_raise(state, unavailable, context)
    raise error


def _refresh_https(
    source: str,
    cache: pathlib.Path,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Conditionally refresh from HTTPS with source-scoped stale fallback."""
    saved = cache_support.read_metadata(context.options.metadata)
    state = _RemoteRequestState(source, cache, context.options.metadata, saved)
    if not _cache_matches_saved_identity(state, context.validate):
        state = _RemoteRequestState(source, cache, context.options.metadata, {})
        _log_decision("cache-identity-mismatch", "https")
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
        return _stale_cache_or_raise(state, unavailable, context)
    with response_context as response:
        try:
            return _remote_response_result(
                state,
                response,
                context,
            )
        except cache_support.NetworkUnavailableError as error:
            return _stale_cache_or_raise(state, error, context)


def refresh(
    source: str | pathlib.Path,
    cache: pathlib.Path,
    validate: ContentValidator,
    options: cache_support.RefreshOptions,
) -> cache_support.RefreshResult:
    """Refresh an untracked cache when its selected authority is newer."""
    context = _RefreshContext(options, validate, cache_support.atomic_write)
    source_text = str(source)
    if options.offline:
        source_name = (
            str(pathlib.Path(source_text).resolve())
            if isinstance(source, pathlib.Path) or "://" not in source_text
            else source_text
        )
        state = _RemoteRequestState(
            source_name,
            cache,
            options.metadata,
            cache_support.read_metadata(options.metadata),
        )
        if not _cache_matches_saved_identity(state, validate):
            message = f"no cached shared dictionary at {cache}"
            raise FileNotFoundError(message)
        _log_decision("offline-cache", "cache")
        return cache_support.RefreshResult("offline-cache", cache)
    if isinstance(source, pathlib.Path) or "://" not in source_text:
        return _refresh_local(pathlib.Path(source_text), cache, context)
    return _refresh_https(source_text, cache, context)
