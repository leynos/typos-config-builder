"""Shared fixtures and fakes for the focused configuration-builder contracts."""

import collections.abc as cabc
from pathlib import Path
from unittest import mock

import pytest

from typos_config_builder import cache

AuthorityFactory = cabc.Callable[..., Path]
#: Authority every seeded cache is bound to, in an unresolvable domain so a
#: leaked request cannot reach a real host.
AUTHORITY_SOURCE = "https://example.invalid/authority.toml"


def authority_text(
    *,
    stem: str = "organ",
    accepted: str = "oxendict",
    ignore: cabc.Sequence[str] = (),
) -> str:
    """Return the smallest complete shared-authority document.

    Ignore patterns are emitted as TOML literal strings so a regular
    expression needs no backslash escaping at the fixture boundary.
    """
    patterns = ", ".join(f"'{pattern}'" for pattern in ignore)
    return (
        "schema = 1\n\n[oxford]\n"
        f'stems = ["{stem}"]\n\n'
        f'[words]\naccepted = ["{accepted}"]\n\n'
        "[words.corrections]\n\n"
        "[phrases.corrections]\n\n"
        f"[patterns]\nignore = [{patterns}]\n\n"
        '[files]\nexclude = [".git"]\n'
    )


def seed_cache(
    repository: Path,
    *,
    saved_digest: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> tuple[Path, Path]:
    """Write a valid cache and the source-bound metadata describing it.

    The digest defaults to the cache's own identity, so passing another
    value models a cache whose bytes no longer match their metadata.
    """
    content = authority_text().encode()
    cache_path = repository / "cache.toml"
    metadata = repository / "cache.json"
    cache_path.write_bytes(content)
    saved: dict[str, object] = {
        "source": AUTHORITY_SOURCE,
        "sha256": cache.digest(content) if saved_digest is None else saved_digest,
    }
    if etag is not None:
        saved["etag"] = etag
    if last_modified is not None:
        saved["last_modified"] = last_modified
    cache.write_metadata(metadata, saved)
    return cache_path, metadata


def fake_response(
    content: bytes,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> mock.MagicMock:
    """Return a context-manager response with a fixed body and validators."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = content
    headers: dict[str, str] = {}
    if etag is not None:
        headers["ETag"] = etag
    if last_modified is not None:
        headers["Last-Modified"] = last_modified
    response.headers = headers
    return response


@pytest.fixture
def authority_factory(tmp_path: Path) -> AuthorityFactory:
    """Create complete authority files outside a repository under test."""

    def create(
        *,
        stem: str = "organ",
        accepted: str = "oxendict",
        ignore: cabc.Sequence[str] = (),
    ) -> Path:
        """Write and return one complete authority fixture."""
        authority = tmp_path / f"authority-{stem}.toml"
        authority.write_text(
            authority_text(stem=stem, accepted=accepted, ignore=ignore),
            encoding="utf-8",
        )
        return authority

    return create


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return an empty repository directory for one builder invocation."""
    path = tmp_path / "repository"
    path.mkdir()
    return path
