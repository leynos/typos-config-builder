"""Provide cache support types and atomic writes for spelling policy."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import email.utils
import hashlib
import json
import pathlib
import tempfile
import typing as typ

ContentValidator = cabc.Callable[[bytes], None]
AtomicWriter = cabc.Callable[[pathlib.Path, bytes], None]


class RemoteResponse(typ.Protocol):
    """Describe the small HTTP response surface used by refresh.

    Attributes
    ----------
    headers
        Response headers used for cache revalidation and metadata.
    """

    headers: cabc.Mapping[str, str]

    def read(self) -> bytes:
        """Read the response body."""
        ...

    def __enter__(self) -> typ.Self:
        """Enter the response context."""
        ...

    def __exit__(self, *_args: object) -> None:
        """Close the response context without suppressing exceptions."""
        ...


Opener = cabc.Callable[..., RemoteResponse]


@dc.dataclass(frozen=True, slots=True)
class RefreshResult:
    """Describe whether refresh changed the untracked cache.

    Attributes
    ----------
    status
        Stable description of the refresh outcome.
    cache
        Path to the validated local cache.
    """

    status: str
    cache: pathlib.Path


@dc.dataclass(frozen=True, slots=True, kw_only=True)
class RefreshOptions:
    """Group metadata, offline, and injectable HTTPS-opening policy.

    Attributes
    ----------
    metadata
        Path to source and freshness metadata stored beside the cache.
    offline
        Whether refresh must avoid authority access.
    opener
        Optional HTTPS opener used in place of the standard library opener.
    """

    metadata: pathlib.Path
    offline: bool = False
    opener: Opener | None = None


class NetworkUnavailableError(OSError):
    """Report that an HTTPS authority could not be reached."""


class InsecureSourceError(ValueError):
    """Report an authority or redirect that does not use HTTPS."""


def atomic_write(path: pathlib.Path, content: bytes) -> None:
    r"""Write bytes beside a destination and atomically replace it.

    Parameters
    ----------
    path
        Destination to replace.
    content
        Bytes to persist.

    Raises
    ------
    OSError
        If the temporary file cannot be written or replace the destination.

    Examples
    --------
    >>> atomic_write(pathlib.Path("policy.toml"), b"schema = 1\n")
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
        ) as stream:
            temporary = pathlib.Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_metadata(path: pathlib.Path) -> dict[str, object]:
    """Read best-effort freshness metadata from an untracked sidecar."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def write_metadata(
    path: pathlib.Path,
    metadata: cabc.Mapping[str, object],
    writer: AtomicWriter = atomic_write,
) -> None:
    """Atomically persist stable JSON freshness metadata."""
    writer(path, (json.dumps(metadata, sort_keys=True) + "\n").encode())


def valid_cache(path: pathlib.Path, validate: ContentValidator) -> bool:
    """Report whether a cache contains a complete shared authority."""
    try:
        validate(path.read_bytes())
    except OSError, UnicodeDecodeError, TypeError, ValueError:
        return False
    return True


def digest(content: bytes) -> str:
    """Return the stable SHA-256 identity of authority bytes."""
    return hashlib.sha256(content).hexdigest()


def remote_is_not_newer(
    saved: cabc.Mapping[str, object], headers: cabc.Mapping[str, str]
) -> bool:
    """Report whether response validators prove an authority is unchanged."""
    etag = headers.get("ETag")
    saved_etag = saved.get("etag")
    if isinstance(etag, str) and isinstance(saved_etag, str):
        return etag == saved_etag
    modified = headers.get("Last-Modified")
    saved_modified = saved.get("last_modified")
    if not isinstance(modified, str) or not isinstance(saved_modified, str):
        return False
    try:
        return email.utils.parsedate_to_datetime(
            modified
        ) <= email.utils.parsedate_to_datetime(saved_modified)
    except TypeError, ValueError:
        return modified == saved_modified


def refresh(
    source: str | pathlib.Path,
    cache: pathlib.Path,
    validate: ContentValidator,
    options: RefreshOptions,
) -> RefreshResult:
    """Refresh a valid untracked cache only when its authority differs.

    Parameters
    ----------
    source
        Local path or HTTPS authority for the shared dictionary.
    cache
        Destination for validated authority bytes.
    validate
        Callback that rejects invalid authority bytes.
    options
        Metadata, offline, and HTTPS-opening settings.

    Returns
    -------
    RefreshResult
        Refresh status and path to the valid local cache.

    Raises
    ------
    FileNotFoundError
        If offline mode has no valid cache or a local source is absent.
    InsecureSourceError
        If an authority or redirect does not use HTTPS.
    NetworkUnavailableError
        If an HTTPS authority is unavailable and no valid matching cache exists.
    ValueError
        If ``validate`` rejects authority or cached content.

    Examples
    --------
    >>> options = RefreshOptions(metadata=pathlib.Path("cache.json"), offline=True)
    >>> refresh(  # doctest: +SKIP
    ...     pathlib.Path("source.toml"),
    ...     pathlib.Path("cache.toml"),
    ...     lambda _: None,
    ...     options,
    ... )
    RefreshResult(status='offline-cache', cache=PosixPath('cache.toml'))
    """
    from typos_config_builder.http import refresh as refresh_from_authority

    return refresh_from_authority(source, cache, validate, options)
