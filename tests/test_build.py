"""Behavioural contracts for building an Oxford spelling configuration."""

import os
import tomllib
from pathlib import Path

import pytest
from conftest import AuthorityFactory, authority_text

from typos_config_builder import ConfigDriftError, build, builder
from typos_config_builder.cache import (
    ContentValidator,
    RefreshOptions,
    RefreshResult,
    atomic_write,
    read_metadata,
)
from typos_config_builder.patterns import validate_local_exceptions
from typos_config_builder.policy import load

# Split intentional misspellings so the test source passes its own spelling gate.
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


def test_local_authority_repairs_cache_and_refreshes_only_when_newer(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """A changed cache is repaired before a newer local authority is refreshed."""
    authority = authority_factory(stem="organ")
    os.utime(authority, ns=(1_000_000_000, 1_000_000_000))
    build(repository, source=authority)
    cache = repository / CACHE_NAME

    cache.write_text(authority_text(stem="local"), encoding="utf-8")
    os.utime(cache, ns=(2_000_000_000, 2_000_000_000))
    build(repository, source=authority)
    assert PLAIN_BRITISH_ORGANIZE in generated_words(repository)

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

    authority = authority_factory()
    build(repository, source=authority)
    (repository / OUTPUT_NAME).unlink()
    build(repository, source=authority, offline=True)

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


def test_undecodable_output_is_drift_and_is_regenerated(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Invalid UTF-8 output is stale rather than an unhandled decode failure."""
    authority = authority_factory()
    build(repository, source=authority)
    output = repository / OUTPUT_NAME
    output.write_bytes(b"\xff")

    with pytest.raises(ConfigDriftError):
        build(repository, source=authority, check=True)

    build(repository, source=authority)
    assert output.read_text(encoding="utf-8").startswith("# Generated")


def test_undecodable_metadata_is_absent(repository: Path) -> None:
    """Invalid UTF-8 metadata is ignored like missing or malformed metadata."""
    metadata = repository / METADATA_NAME
    metadata.write_bytes(b"\xff")

    assert read_metadata(metadata) == {}


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b"3", b'"text"'])
def test_metadata_that_is_not_a_json_object_is_absent(
    repository: Path,
    payload: bytes,
) -> None:
    """Metadata that is not a JSON object carries no validators.

    Ported from the ``weaver`` fork. The readable control comes first, so a
    reader that always returned nothing would fail this test.
    """
    metadata = repository / METADATA_NAME
    metadata.write_bytes(b'{"source": "control"}')
    assert read_metadata(metadata) == {"source": "control"}

    metadata.write_bytes(payload)

    assert read_metadata(metadata) == {}


@pytest.mark.parametrize("pattern", ["**/**", "**/*.*"])
def test_broad_file_glob_equivalents_are_rejected(pattern: str) -> None:
    """Equivalent all-file globs cannot disable repository spelling checks."""
    with pytest.raises(ValueError, match="local file exclusion is too broad"):
        validate_local_exceptions((), (pattern,))


def test_bundled_authority_contains_handwritten_policy(repository: Path) -> None:
    """The authority accepts the compound and records hyphen correction metadata."""
    build(repository, source=builder.bundled_authority())

    words = generated_words(repository)
    cached = tomllib.loads((repository / CACHE_NAME).read_text(encoding="utf-8"))
    assert words["handwritten"] == "handwritten"
    assert cached["phrases"]["corrections"][HYPHENATED_HANDWRITTEN] == "handwritten"


def test_default_source_is_live_authority(
    monkeypatch: pytest.MonkeyPatch,
    repository: Path,
) -> None:
    """Omitting a source selects the live authority with a bundled bootstrap."""
    captured: dict[str, object] = {}

    def fake_refresh(
        source: str | Path,
        cache_path: Path,
        validate: ContentValidator,
        options: RefreshOptions,
    ) -> RefreshResult:
        """Record the selected authority and seed the cache without a fetch."""
        captured["source"] = source
        captured["bootstrap"] = options.bootstrap
        atomic_write(cache_path, builder.bundled_authority().read_bytes())
        return RefreshResult("refreshed", cache_path)

    monkeypatch.setattr(builder.cache, "refresh", fake_refresh)

    build(repository)

    assert captured["source"] == builder.DEFAULT_SOURCE
    assert builder.DEFAULT_SOURCE.startswith("https://raw.githubusercontent.com/")
    assert captured["bootstrap"] == builder.bundled_authority()


SHARED_IGNORE_PATTERN = r"\bSPDX-[A-Za-z0-9.-]+"
ABSENT_IGNORE_PATTERN = r"\bRFC-[0-9]+"


def generated_ignore_patterns(repository: Path) -> list[str]:
    """Load the generated Typos ignore expressions from a repository."""
    generated = tomllib.loads((repository / OUTPUT_NAME).read_text(encoding="utf-8"))
    return generated["default"]["extend-ignore-re"]


def write_overlay(repository: Path, body: str) -> Path:
    """Write a sparse overlay containing one ``[patterns]`` table."""
    overlay = repository / "typos.local.toml"
    overlay.write_text(f"schema = 1\n\n[patterns]\n{body}", encoding="utf-8")
    return overlay


def test_local_patterns_remove_withdraws_shared_pattern(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """An overlay withdrawal drops a shared ignore pattern from the output."""
    authority = authority_factory(ignore=(SHARED_IGNORE_PATTERN,))
    write_overlay(repository, f"remove = ['{SHARED_IGNORE_PATTERN}']\n")

    build(repository, source=authority)

    assert SHARED_IGNORE_PATTERN not in generated_ignore_patterns(repository)


def test_removing_an_absent_pattern_is_a_no_op(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """Withdrawing a pattern the shared base lacks is accepted and changes nothing."""
    authority = authority_factory(ignore=(SHARED_IGNORE_PATTERN,))
    overlay = write_overlay(repository, f"remove = ['{ABSENT_IGNORE_PATTERN}']\n")

    build(repository, source=authority)

    assert load(overlay, sparse=True).removed_patterns == (ABSENT_IGNORE_PATTERN,)
    assert generated_ignore_patterns(repository) == [SHARED_IGNORE_PATTERN]


def test_overlay_cannot_both_ignore_and_remove_a_pattern(
    authority_factory: AuthorityFactory,
    repository: Path,
) -> None:
    """A contradictory overlay is rejected rather than resolved silently."""
    authority = authority_factory(ignore=(SHARED_IGNORE_PATTERN,))
    write_overlay(
        repository,
        f"ignore = ['{SHARED_IGNORE_PATTERN}']\nremove = ['{SHARED_IGNORE_PATTERN}']\n",
    )

    with pytest.raises(ValueError, match="both ignores and removes patterns"):
        build(repository, source=authority)
