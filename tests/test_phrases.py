"""Contracts for shared phrase-correction enforcement."""

from __future__ import annotations

import pathlib
import shutil
import subprocess  # noqa: S404
import typing as typ

import pytest
from conftest import (
    CORRECTION,
    PROHIBITED,
    build_repository,
    cache_text,
)
from hypothesis import event, given
from hypothesis import strategies as st

from typos_config_builder import phrases

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# The prohibited phrase's title-cased variant is spliced so this file never
# contains it literally.
TITLE_PROHIBITED = "Hand" + "-written"
INLINE_CODE = r"`[^`\n]+`"
FENCED_BLOCK = "(?s)```.*?```"
ANGLE_SPAN = r"<[^>\n]+>"
# Placeholder commit identifier for a submodule gitlink. Git records a
# gitlink without resolving the object, so the commit need not exist.
GITLINK_COMMIT = "0" * 39 + "1"


def overlay_text(*, corrections: cabc.Sequence[tuple[str, str]]) -> str:
    """Return a sparse overlay contributing extra phrase corrections."""
    entries = "".join(
        f'"{phrase}" = "{correction}"\n' for phrase, correction in corrections
    )
    return f"schema = 1\n\n[phrases.corrections]\n{entries}"


def _git(repository: pathlib.Path, *arguments: str) -> None:
    """Run one Git command inside a fixture repository."""
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def bare_policy(
    *, ignore: cabc.Sequence[str] = (), exclude: cabc.Sequence[str] = ()
) -> phrases.PhrasePolicy:
    """Return an in-memory policy holding only the prohibited phrase."""
    return phrases.PhrasePolicy(
        phrase_corrections=((PROHIBITED, CORRECTION),),
        ignore_patterns=tuple(ignore),
        excluded_files=tuple(exclude),
    )


def test_load_policy_merges_shared_and_overlay_phrases(tmp_path: pathlib.Path) -> None:
    """Shared corrections, overlay corrections, and scan policy are combined."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(
                ignore=(INLINE_CODE,), exclude=(".git", "*.md", "!README.md")
            ),
            "typos.local.toml": overlay_text(
                corrections=(("fit-for-purpose", "suitable"),)
            ),
        },
    )

    policy = phrases.load_policy(repository)

    # ``policy.Dictionary`` normalizes every list by sorting it, so the
    # overlay's phrase joins shared policy in sorted order.
    assert policy.phrase_corrections == (
        ("fit-for-purpose", "suitable"),
        (PROHIBITED, CORRECTION),
    )
    assert policy.ignore_patterns == (INLINE_CODE,)
    assert policy.excluded_files == ("!README.md", "*.md", ".git")


def test_load_policy_requires_a_generated_cache(tmp_path: pathlib.Path) -> None:
    """A missing cache names the builder rather than the bare path."""
    repository = build_repository(tmp_path, {"README.md": "Nothing to see.\n"})

    with pytest.raises(FileNotFoundError, match="typos-config-builder"):
        phrases.load_policy(repository)


def test_findings_report_exact_location_and_original_case(
    tmp_path: pathlib.Path,
) -> None:
    """Each finding carries a one-based location and the text as written."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": f"Prefer {PROHIBITED}.\n\nAlso {TITLE_PROHIBITED} prose.\n",
        },
    )

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert [
        (str(item.path), item.line, item.column, item.phrase, item.correction)
        for item in findings
    ] == [
        ("README.md", 1, 8, PROHIBITED, CORRECTION),
        ("README.md", 3, 6, TITLE_PROHIBITED, CORRECTION),
    ]


def test_boundaries_exclude_adjacent_word_characters(tmp_path: pathlib.Path) -> None:
    """Hyphen-adjacent and word-adjacent occurrences are not reported."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": (
                "pre-" + PROHIBITED + "\n" + PROHIBITED + "-ly\n" + PROHIBITED + "\n"
            ),
        },
    )

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert [(item.line, item.column) for item in findings] == [(3, 1)]


def test_ignore_patterns_mask_inline_code_and_fenced_blocks(
    tmp_path: pathlib.Path,
) -> None:
    """Occurrences inside ignored spans never become findings."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(ignore=(INLINE_CODE, FENCED_BLOCK)),
            "README.md": (
                f"Inline `{PROHIBITED}` stays quiet.\n\n"
                "```\n" + PROHIBITED + "\n```\n\n"
                f"Bare {PROHIBITED} is reported.\n"
            ),
        },
    )

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert [(item.line, item.column) for item in findings] == [(7, 6)]


def test_excluded_globs_apply_in_normalized_order(tmp_path: pathlib.Path) -> None:
    """Exclusions use gitignore semantics over the policy's sorted order.

    Sorting places ``!README.md`` before ``*.md``, and gitignore gives the
    last matching pattern priority, so the re-inclusion is inert. The gate
    mirrors the generated configuration rather than inventing another order.
    """
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(
                exclude=(".git", "*.md", "!README.md")
            ),
            "README.md": f"{PROHIBITED}\n",
            "docs/guide.md": f"{PROHIBITED}\n",
            "notes.txt": f"{PROHIBITED}\n",
        },
    )

    findings = phrases.find_phrases(repository, phrases.load_policy(repository))

    assert [str(item.path) for item in findings] == ["notes.txt"]


def test_tracked_symlinks_are_skipped(tmp_path: pathlib.Path) -> None:
    """A tracked symlink is never followed out of the repository."""
    repository = build_repository(tmp_path, {".typos-oxendict-base.toml": cache_text()})
    external = tmp_path / "external.txt"
    external.write_text(f"{PROHIBITED}\n", encoding="utf-8")
    (repository / "external.txt").symlink_to(external)
    _git(repository, "add", "external.txt")

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the scan followed a tracked symlink out of the repository"
    )


def test_tracked_path_behind_a_symlinked_parent_is_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """A symlinked parent directory cannot smuggle outside text into the scan."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "docs/guide.md": "Ordinary prose only.\n",
        },
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "guide.md").write_text(f"{PROHIBITED}\n", encoding="utf-8")
    shutil.rmtree(repository / "docs")
    (repository / "docs").symlink_to(outside, target_is_directory=True)

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the scan read a file outside the repository through a symlinked parent"
    )


def test_policy_documents_are_not_scanned(tmp_path: pathlib.Path) -> None:
    """The cache, overlay, metadata, and generated config are skipped."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            ".typos-oxendict-base.json": f'{{"note": "{PROHIBITED}"}}\n',
            "typos.local.toml": overlay_text(corrections=((PROHIBITED, CORRECTION),)),
            "typos.toml": f"# Policy for {PROHIBITED} corrections.\n",
        },
    )

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "a policy document was reported as a finding"
    )


def test_tracked_file_removed_after_enumeration_fails_closed(
    tmp_path: pathlib.Path,
) -> None:
    """A vanished tracked file raises rather than silently passing."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    (repository / "README.md").unlink()

    with pytest.raises(phrases.PhraseScanError) as error:
        phrases.find_phrases(repository, phrases.load_policy(repository))

    assert error.value.path == pathlib.Path("README.md")
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_tracked_submodule_directory_is_skipped(tmp_path: pathlib.Path) -> None:
    """A tracked submodule gitlink is skipped instead of failing the scan.

    The gitlink is recorded with ``git update-index --cacheinfo`` and an empty
    directory is created in its place. That route avoids committing an inner
    repository and relaxing Git's file-protocol policy, while reproducing what
    ``git ls-files`` reports for a checked-out submodule.
    """
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Ordinary prose.\n"},
    )
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{GITLINK_COMMIT},sub",
    )
    (repository / "sub").mkdir()
    (repository / "sub" / "NOTES.md").write_text(
        f"Prefer {PROHIBITED}.\n", encoding="utf-8"
    )

    assert not phrases.find_phrases(repository, phrases.load_policy(repository)), (
        "the submodule's untracked content was scanned"
    )


def test_undecodable_tracked_file_fails_closed(tmp_path: pathlib.Path) -> None:
    """Tracked bytes that are not UTF-8 raise rather than being skipped."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Initially UTF-8.\n"},
    )
    (repository / "README.md").write_bytes(b"\xff\xfe")

    with pytest.raises(phrases.PhraseScanError) as error:
        phrases.find_phrases(repository, phrases.load_policy(repository))

    assert error.value.path == pathlib.Path("README.md")
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


def test_tracked_files_requires_a_git_repository(tmp_path: pathlib.Path) -> None:
    """Enumeration outside a repository surfaces the Git failure."""
    outside = tmp_path / "not-a-repository"
    outside.mkdir()

    with pytest.raises(subprocess.CalledProcessError):
        phrases.tracked_files(outside)


def test_mask_marks_overlapping_spans_against_the_original_text() -> None:
    """Overlapping ignore patterns mask the union of their original spans.

    Blanking each pattern in turn would destroy the delimiters the next
    pattern needs, leaving the second span readable.
    """
    text = "`x<y` " + PROHIBITED + ">z"

    masked = phrases.mask(text, (INLINE_CODE, ANGLE_SPAN))

    assert len(masked) == len(text)
    assert masked == " " * (len(text) - 1) + "z"
    assert not phrases.scan_text(
        pathlib.Path("README.md"),
        text,
        bare_policy(ignore=(INLINE_CODE, ANGLE_SPAN)),
    ), "an overlapping ignored span left the phrase readable"


BOUNDARY_CHARACTERS = st.sampled_from(("", " ", "\n", ".", "a", "_", "-"))


@given(left=BOUNDARY_CHARACTERS, right=BOUNDARY_CHARACTERS)
def test_phrase_boundaries_hold_for_generated_neighbours(left: str, right: str) -> None:
    """INV-5: a phrase is reported exactly when both neighbours permit it."""
    text = f"{left}{PROHIBITED}{right}"
    findings = phrases.scan_text(pathlib.Path("README.md"), text, bare_policy())
    has_permitting_boundaries = left not in {"a", "_", "-"} and right not in {
        "a",
        "_",
        "-",
    }
    event(f"boundaries permit a finding: {has_permitting_boundaries}")

    assert bool(findings) is has_permitting_boundaries
    if findings:
        assert (findings[0].line, findings[0].column) == (
            text.count("\n", 0, len(left)) + 1,
            len(left.rsplit("\n", maxsplit=1)[-1]) + 1,
        )


@given(content=st.text(alphabet=" abcdefghijklmnopqrstuvwxyz-", max_size=40))
def test_generated_ignored_spans_stay_masked(content: str) -> None:
    """INV-4: masking preserves offsets and hides phrases inside ignored spans.

    Negative control: replacing mark-then-blank with a sequential
    ``re.sub`` per pattern still passes this single-pattern property but
    fails the overlapping-span example above.
    """
    # Spaces keep the phrase boundaries permitting, so the unmasked control
    # below reports a finding for every generated example.
    text = f"`{content} {PROHIBITED} {content}`"
    event(f"surrounding content is empty: {not content}")

    masked = phrases.mask(text, (INLINE_CODE,))

    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    path = pathlib.Path("README.md")
    assert not phrases.scan_text(path, text, bare_policy(ignore=(INLINE_CODE,))), (
        "a phrase inside an ignored span escaped masking"
    )
    assert phrases.scan_text(path, text, bare_policy()), (
        "the unmasked control found nothing, so the property is vacuous"
    )
