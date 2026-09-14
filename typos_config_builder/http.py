"""Refresh spelling policy from source-scoped local or HTTPS authorities.

This module owns the local authority path and the entry point that selects
between local, offline, and HTTPS refresh. The HTTPS path itself lives in
:mod:`typos_config_builder.remote`, which also owns the cache-identity
primitives shared by both paths.

Diagnostics expose only bounded decisions, source kinds, and error classes;
authority URLs and local paths are deliberately excluded from logs.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import pathlib

from typos_config_builder import cache as cache_support
from typos_config_builder import remote

ContentValidator = cabc.Callable[[bytes], None]
AtomicWriter = cabc.Callable[[pathlib.Path, bytes], None]
# Re-exported so callers keep one import site for the remote refresh limits.
HTTP_NOT_MODIFIED = remote.HTTP_NOT_MODIFIED
TRANSIENT_HTTP_STATUSES = remote.TRANSIENT_HTTP_STATUSES
MAX_AUTHORITY_BYTES = remote.MAX_AUTHORITY_BYTES


@dc.dataclass(frozen=True, slots=True)
class _LocalSourceState:
    """Group local authority identity and freshness state."""

    name: str
    mtime_ns: int


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
        and remote.cache_matches_saved_digest(cache, saved, validate)
    )


def _refresh_local(
    source: pathlib.Path,
    cache: pathlib.Path,
    context: remote.RefreshContext,
) -> cache_support.RefreshResult:
    """Refresh from a local authority only when it is newer."""
    source_stat = source.stat()
    source_state = _LocalSourceState(
        name=str(source.resolve()),
        mtime_ns=source_stat.st_mtime_ns,
    )
    saved = cache_support.read_metadata(context.options.metadata)
    if _local_cache_is_current(cache, saved, source_state, context.validate):
        remote.log_decision("current", "local")
        return cache_support.RefreshResult("current", cache)
    decision = (
        "source-mismatch" if saved.get("source") != source_state.name else "newer"
    )
    remote.log_decision(decision, "local")
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


def _offline_source_name(source: str | pathlib.Path, source_text: str) -> str:
    """Return the identity an offline cache must have been written for."""
    is_local = isinstance(source, pathlib.Path) or "://" not in source_text
    return str(pathlib.Path(source_text).resolve()) if is_local else source_text


def _refresh_offline(
    source: str | pathlib.Path,
    source_text: str,
    cache: pathlib.Path,
    context: remote.RefreshContext,
) -> cache_support.RefreshResult:
    """Reuse a source-matching cache, or seed one from a configured snapshot."""
    state = remote.RemoteRequestState(
        _offline_source_name(source, source_text),
        cache,
        context.options.metadata,
        cache_support.read_metadata(context.options.metadata),
    )
    if not remote.cache_matches_saved_identity(state, context.validate):
        bootstrapped = remote.bootstrap_or_none(state, context)
        if bootstrapped is None:
            message = f"no cached shared dictionary at {cache}"
            raise FileNotFoundError(message)
        return bootstrapped
    remote.log_decision("offline-cache", "cache")
    return cache_support.RefreshResult("offline-cache", cache)


def refresh(
    source: str | pathlib.Path,
    cache: pathlib.Path,
    validate: ContentValidator,
    options: cache_support.RefreshOptions,
) -> cache_support.RefreshResult:
    """Refresh an untracked cache when its selected authority is newer.

    Parameters
    ----------
    source
        Local path or HTTPS authority for the shared dictionary.
    cache
        Destination for validated authority bytes.
    validate
        Callback that rejects invalid authority bytes.
    options
        Metadata, offline, bootstrap, and HTTPS-opening settings.

    Returns
    -------
    cache_support.RefreshResult
        Refresh status and path to the valid local cache.

    Raises
    ------
    FileNotFoundError
        If offline mode has no valid cache and no snapshot is configured, or
        a local source is absent.

    Examples
    --------
    >>> refresh(source, cache, validate, options)  # doctest: +SKIP
    RefreshResult(status='refreshed', cache=PosixPath('cache.toml'))
    """
    context = remote.RefreshContext(options, validate, cache_support.atomic_write)
    source_text = str(source)
    if options.offline:
        return _refresh_offline(source, source_text, cache, context)
    if isinstance(source, pathlib.Path) or "://" not in source_text:
        return _refresh_local(pathlib.Path(source_text), cache, context)
    return remote.refresh_https(source_text, cache, context)
