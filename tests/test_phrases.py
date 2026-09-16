"""Contracts for shared phrase-correction enforcement."""

from __future__ import annotations

import pathlib
import typing as typ

import pytest
from conftest import (
    CORRECTION,
    PROHIBITED,
    build_repository,
    cache_text,
    overlay_text,
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
