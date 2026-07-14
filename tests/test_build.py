"""Behavioural contracts for building an Oxford spelling configuration."""

import os
import tomllib
from pathlib import Path

import pytest
from conftest import AuthorityFactory, authority_text

from typos_config_builder import ConfigDriftError, build
from typos_config_builder.cache import atomic_write

PLAIN_BRITISH_ORGANIZE = "organi" + "se"
HYPHENATED_HANDWRITTEN = "hand" + "-written"
CACHE_NAME = ".typos-oxendict-base.toml"
METADATA_NAME = ".typos-oxendict-base.json"
OUTPUT_NAME = "typos.toml"
REPLACEMENT_FAILURE = "replacement failure"


def generated_words(repository: Path) -> dict[str, str]:
    """Load the generated Typos word mappings from a repository."""
    generated = tomllib.loads((repository / OUTPUT_NAME).read_text(encoding="utf-8"))
    return generated["default"]["extend-words"]


def test_build_merges_sparse_local_dictionary(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """A sparse repository overlay augments the shared dictionary."""
    authority = authority_factory()
    (repository / "typos.local.toml").write_text(
        'schema = 1\n\n[words]\naccepted = ["LocalWidget"]\n\n'
        '[words.corrections]\nteh = "the"\n',
        encoding="utf-8",
    )

    build(repository, source=authority)

    words = generated_words(repository)
    assert words["LocalWidget"] == "LocalWidget"
    assert words["teh"] == "the"
    assert words[PLAIN_BRITISH_ORGANIZE] == "organize"


def test_build_is_deterministic_and_leaves_no_temporary_file(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Identical inputs produce identical bytes through an atomic replacement."""
    authority = authority_factory()

    build(repository, source=authority)
    first = (repository / OUTPUT_NAME).read_bytes()
    build(repository, source=authority)

    assert (repository / OUTPUT_NAME).read_bytes() == first
    assert {path.name for path in repository.iterdir()} == {
        CACHE_NAME,
        METADATA_NAME,
        OUTPUT_NAME,
    }


def test_atomic_write_preserves_output_when_replacement_fails(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    """A failed replacement removes its temporary file and preserves output."""
    output = repository / OUTPUT_NAME
    output.write_bytes(b"previous\n")
    temporary_paths: list[Path] = []

    def fail_replace(temporary: Path, target: Path) -> None:
        """Record the temporary path and simulate replacement failure."""
        temporary_paths.append(temporary)
        assert target == output
        raise OSError(REPLACEMENT_FAILURE)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match=REPLACEMENT_FAILURE):
        atomic_write(output, b"replacement\n")

    assert output.read_bytes() == b"previous\n"
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()


def test_local_authority_refreshes_only_when_newer(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """A newer cache survives an older local authority until the source advances."""
    authority = authority_factory(stem="organ")
    os.utime(authority, ns=(1_000_000_000, 1_000_000_000))
    build(repository, source=authority)
    cache = repository / CACHE_NAME

    cache.write_text(authority_text(stem="local"), encoding="utf-8")
    os.utime(cache, ns=(2_000_000_000, 2_000_000_000))
    build(repository, source=authority)
    assert generated_words(repository)["localize"] == "localize"

    authority.write_text(authority_text(stem="newer"), encoding="utf-8")
    os.utime(authority, ns=(3_000_000_000, 3_000_000_000))
    build(repository, source=authority)
    assert generated_words(repository)["newerize"] == "newerize"


def test_offline_build_requires_and_reuses_valid_cache(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Offline operation fails without a cache and otherwise performs no fetch."""
    remote = "https://example.invalid/authority.toml"

    with pytest.raises(FileNotFoundError, match="cached shared dictionary"):
        build(repository, source=remote, offline=True)

    build(repository, source=authority_factory())
    (repository / OUTPUT_NAME).unlink()
    build(repository, source=remote, offline=True)

    assert (repository / OUTPUT_NAME).is_file()


def test_check_accepts_current_output_without_rewriting(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Check mode accepts current output and leaves its bytes untouched."""
    authority = authority_factory()
    build(repository, source=authority)
    output = repository / OUTPUT_NAME
    before = output.read_bytes()

    build(repository, source=authority, check=True)

    assert output.read_bytes() == before


def test_check_rejects_missing_and_drifted_output(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Check mode reports both absent and stale generated configuration."""
    authority = authority_factory()

    with pytest.raises(
        ConfigDriftError,
        match="generated configuration is stale",
    ) as missing:
        build(repository, source=authority, check=True)
    assert missing.value.output == repository / OUTPUT_NAME

    build(repository, source=authority)
    (repository / OUTPUT_NAME).write_text("# stale\n", encoding="utf-8")
    with pytest.raises(
        ConfigDriftError,
        match="generated configuration is stale",
    ) as drifted:
        build(repository, source=authority, check=True)
    assert drifted.value.output == repository / OUTPUT_NAME


def test_bundled_authority_contains_handwritten_policy(repository: Path) -> None:
    """The installed authority accepts the closed compound and corrects the hyphen."""
    build(repository)

    words = generated_words(repository)
    cached = tomllib.loads((repository / CACHE_NAME).read_text(encoding="utf-8"))
    assert words["handwritten"] == "handwritten"
    assert cached["phrases"]["corrections"][HYPHENATED_HANDWRITTEN] == "handwritten"
