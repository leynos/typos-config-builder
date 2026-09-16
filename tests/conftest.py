"""Shared fixtures and fakes for the builder's tests.

Provides the authority text and cache-seeding helpers, a fake HTTP response
factory, a tracked-file git repository factory, the recording Typos runner,
and the prohibited-phrase constants shared across the phrase, gate, and CLI
contracts. Test modules import from this module rather than from each other.
"""

import collections.abc as cabc
import dataclasses as dc
import json
import pathlib

# The gate's contracts inspect the command line it assembles for the pinned
# Typos binary, so the fakes need the same completion type the gate returns.
import subprocess  # noqa: S404 - only CompletedProcess and DEVNULL are used.
from pathlib import Path
from unittest import mock

import pytest

from typos_config_builder import cache

AuthorityFactory = cabc.Callable[..., Path]
#: Authority every seeded cache is bound to, in an unresolvable domain so a
#: leaked request cannot reach a real host.
AUTHORITY_SOURCE = "https://example.invalid/authority.toml"
# The prohibited phrase is spliced so this file never contains it literally.
PROHIBITED = "hand" + "-written"
CORRECTION = "handwritten"


@dc.dataclass(frozen=True, slots=True)
class RunnerCall:
    """Record one invocation of the injected Typos runner.

    Attributes
    ----------
    argv
        Complete command line the gate assembled.
    cwd
        Working directory the gate selected.
    has_config
        Whether generated configuration existed when the call was made.
    """

    argv: tuple[str, ...]
    cwd: pathlib.Path
    has_config: bool


class FakeRunner:
    """Record Typos invocations and replay configured exit codes."""

    def __init__(self, *returncodes: int) -> None:
        """Queue exit codes, repeating the last once the queue is exhausted."""
        self._returncodes = returncodes or (0,)
        self.calls: list[RunnerCall] = []

    def __call__(
        self,
        args: cabc.Sequence[str],
        /,
        *,
        cwd: pathlib.Path,
        check: bool,
        stdin: int,
    ) -> subprocess.CompletedProcess[bytes]:
        """Record one invocation and return its configured completion.

        A shared fake cannot use a bare assertion, because ``conftest`` is not
        rewritten by pytest, so each misuse fails the calling test explicitly.
        """
        if check:
            pytest.fail("the gate must inspect exit codes rather than raise")
        if stdin != subprocess.DEVNULL:
            pytest.fail("Typos must not inherit standard input")
        index = min(len(self.calls), len(self._returncodes) - 1)
        self.calls.append(RunnerCall(tuple(args), cwd, (cwd / "typos.toml").is_file()))
        return subprocess.CompletedProcess(list(args), self._returncodes[index])


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
    patterns = ", ".join(json.dumps(pattern) for pattern in ignore)
    return (
        "schema = 1\n\n[oxford]\n"
        f'stems = ["{stem}"]\n\n'
        f'[words]\naccepted = ["{accepted}"]\n\n'
        "[words.corrections]\n\n"
        "[phrases.corrections]\n\n"
        f"[patterns]\nignore = [{patterns}]\n\n"
        '[files]\nexclude = [".git"]\n'
    )


def overlay_text(*, corrections: cabc.Sequence[tuple[str, str]]) -> str:
    """Return a sparse overlay contributing extra phrase corrections."""
    entries = "".join(
        f"{json.dumps(phrase)} = {json.dumps(correction)}\n"
        for phrase, correction in corrections
    )
    return f"schema = 1\n\n[phrases.corrections]\n{entries}"


def cache_text(
    *,
    corrections: cabc.Sequence[tuple[str, str]] = ((PROHIBITED, CORRECTION),),
    ignore: cabc.Sequence[str] = (),
    exclude: cabc.Sequence[str] = (".git",),
) -> str:
    """Return a complete authority document carrying phrase corrections."""
    entries = "".join(
        f"{json.dumps(phrase)} = {json.dumps(correction)}\n"
        for phrase, correction in corrections
    )
    excludes = ", ".join(json.dumps(item) for item in exclude)
    return (
        authority_text(ignore=ignore)
        .replace("[phrases.corrections]\n", f"[phrases.corrections]\n{entries}")
        .replace('exclude = [".git"]', f"exclude = [{excludes}]")
    )


def _git(repository: pathlib.Path, *arguments: str) -> None:
    """Run one Git command inside a fixture repository."""
    subprocess.run(  # noqa: S603 - only fixed, trusted fixture arguments.
        ["git", "-C", str(repository), *arguments],  # noqa: S607 - git is
        # intentionally resolved from PATH; fixtures need no absolute path.
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def build_repository(
    root: pathlib.Path, files: cabc.Mapping[str, str], *, name: str = "repository"
) -> pathlib.Path:
    """Create and stage a tracked-file fixture repository."""
    repository = root / name
    repository.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repository, "init", "--quiet")
    _git(repository, "add", "-A")
    return repository


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
    # A real response streams its body once and then returns b"" at the end,
    # so the bounded reader's loop must see the same sequence.
    response.read.side_effect = [content, b""]
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


@pytest.fixture
def authority(tmp_path: Path) -> Path:
    """Return a local authority carrying the prohibited phrase and one stem."""
    path = tmp_path / "authority.toml"
    path.write_text(cache_text(), encoding="utf-8")
    return path
