"""Refresh spelling policy from source-scoped local or HTTPS authorities.

Diagnostics expose only bounded decisions, source kinds, and error classes;
authority URLs and local paths are deliberately excluded from logs.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import logging
import pathlib
import typing as typ
import urllib.error
import urllib.parse
import urllib.request

from typos_config_builder import cache as cache_support

if typ.TYPE_CHECKING:
    import http.client

ContentValidator = cabc.Callable[[bytes], None]
AtomicWriter = cabc.Callable[[pathlib.Path, bytes], None]
HTTP_NOT_MODIFIED = 304
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
        cache_support.valid_cache(cache, validate)
        and saved.get("source") == source.name
        and isinstance(saved_mtime, int)
        and source.mtime_ns <= saved_mtime
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
    except urllib.error.URLError as error:
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
    if cache_support.valid_cache(
        state.cache, context.validate
    ) and cache_support.remote_is_not_newer(state.saved, response.headers):
        _log_decision("current", "https")
        return cache_support.RefreshResult("current", state.cache)
    return _write_remote_cache(state, response, context)


def _stale_cache_or_raise(
    cache: pathlib.Path,
    error: cache_support.NetworkUnavailableError,
    context: _RefreshContext,
    *,
    has_matching_source: bool,
) -> cache_support.RefreshResult:
    """Return a source-scoped stale cache or propagate connectivity loss."""
    if has_matching_source and cache_support.valid_cache(cache, context.validate):
        _log_decision(
            "stale-cache",
            "https",
            error_class="network-unavailable",
            level=logging.INFO,
        )
        return cache_support.RefreshResult("stale-cache", cache)
    _log_decision(
        "stale-cache-rejected",
        "https",
        error_class="network-unavailable",
        level=logging.WARNING,
    )
    raise error


def _is_current_not_modified_response(
    cache: pathlib.Path,
    error: urllib.error.HTTPError,
    context: _RefreshContext,
    *,
    has_matching_source: bool,
) -> bool:
    """Return whether HTTP 304 confirms the matching cache is current."""
    return (
        error.code == HTTP_NOT_MODIFIED
        and has_matching_source
        and cache_support.valid_cache(cache, context.validate)
    )


def _http_error_result(
    cache: pathlib.Path,
    error: urllib.error.HTTPError,
    context: _RefreshContext,
    *,
    has_matching_source: bool,
) -> cache_support.RefreshResult:
    """Translate HTTP 304 into a source-scoped current-cache result."""
    if _is_current_not_modified_response(
        cache,
        error,
        context,
        has_matching_source=has_matching_source,
    ):
        _log_decision("not-modified", "https", error_class="http-not-modified")
        return cache_support.RefreshResult("current", cache)
    if error.code == HTTP_NOT_MODIFIED:
        _log_decision(
            "not-modified-rejected",
            "https",
            error_class="http-not-modified",
            level=logging.WARNING,
        )
    raise error


def _refresh_https(
    source: str,
    cache: pathlib.Path,
    context: _RefreshContext,
) -> cache_support.RefreshResult:
    """Conditionally refresh from HTTPS with source-scoped stale fallback."""
    saved = cache_support.read_metadata(context.options.metadata)
    has_matching_source = saved.get("source") == source
    if not has_matching_source:
        saved = {}
        _log_decision("source-mismatch", "https")
    request = _https_request(source, _conditional_headers(saved))
    open_remote = (
        _HTTPS_OPENER.open if context.options.opener is None else context.options.opener
    )
    try:
        response_context = open_remote(request, timeout=30.0)
    except urllib.error.HTTPError as error:
        return _http_error_result(
            cache,
            error,
            context,
            has_matching_source=has_matching_source,
        )
    except urllib.error.URLError:
        message = f"shared dictionary authority is unavailable: {source}"
        unavailable = cache_support.NetworkUnavailableError(message)
        return _stale_cache_or_raise(
            cache,
            unavailable,
            context,
            has_matching_source=has_matching_source,
        )
    with response_context as response:
        try:
            return _remote_response_result(
                _RemoteRequestState(source, cache, context.options.metadata, saved),
                response,
                context,
            )
        except cache_support.NetworkUnavailableError as error:
            return _stale_cache_or_raise(
                cache,
                error,
                context,
                has_matching_source=has_matching_source,
            )


def refresh(
    source: str | pathlib.Path,
    cache: pathlib.Path,
    validate: ContentValidator,
    options: cache_support.RefreshOptions,
) -> cache_support.RefreshResult:
    """Refresh an untracked cache when its selected authority is newer."""
    context = _RefreshContext(options, validate, cache_support.atomic_write)
    if options.offline:
        if not cache_support.valid_cache(cache, validate):
            message = f"no cached shared dictionary at {cache}"
            raise FileNotFoundError(message)
        _log_decision("offline-cache", "cache")
        return cache_support.RefreshResult("offline-cache", cache)
    source_text = str(source)
    if isinstance(source, pathlib.Path) or "://" not in source_text:
        return _refresh_local(pathlib.Path(source_text), cache, context)
    return _refresh_https(source_text, cache, context)
