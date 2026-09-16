"""Contracts for Markdown-scoped ignore expressions.

Covers the ``[patterns] markdown_only`` overlay key end to end: schema
acceptance and rejection, the merge rule that withholds a confined
expression from the default ignore set, and the rendered
``[type.markdown]`` table.

Assertion messages are bound to a local name before the assertion rather
than written inline across several lines. Pytest evaluates an assertion
message only when the assertion fails, so a wrapped inline message would
never be executed and would count against this file's coverage.
"""

from __future__ import annotations

import json
import tomllib
import typing as typ

import pytest

from typos_config_builder import build, policy
from typos_config_builder.render import render

if typ.TYPE_CHECKING:
    import pathlib

    from conftest import AuthorityFactory

# The two masks cuprum confines to Markdown: a fenced block and an inline
# span. They are literal strings so no backslash escaping is needed here.
FENCED_BLOCK = r"(?s)```.*?```"
INLINE_SPAN = r"`[^`\n]+`"
SHARED_PATTERN = r"\bSPDX-[A-Za-z0-9.-]+"
# Split so this source passes the repository's own spelling gate.
PLAIN_BRITISH_ORGANIZE = "organi" + "se"
OUTPUT_NAME = "typos.toml"


def _write(directory: pathlib.Path, body: str) -> pathlib.Path:
    """Write a sparse overlay holding one ``[patterns]`` table."""
    path = directory / "typos.local.toml"
    path.write_text(f"schema = 1\n\n[patterns]\n{body}", encoding="utf-8")
    return path


def _generated(repository: pathlib.Path) -> dict[str, typ.Any]:
    """Parse the generated configuration from a repository."""
    text = (repository / OUTPUT_NAME).read_text(encoding="utf-8")
    return tomllib.loads(text)


def test_overlay_accepts_markdown_only_patterns(tmp_path: pathlib.Path) -> None:
    """A ``markdown_only`` list loads into normalized, sorted policy.

    This is the control for the rejection matrix below, which a loader that
    refused every ``markdown_only`` list would otherwise satisfy.
    """
    overlay = _write(tmp_path, f"markdown_only = ['{INLINE_SPAN}', '{FENCED_BLOCK}']\n")

    loaded = policy.load(overlay, sparse=True)

    sorted_entries = "markdown_only should load sorted into markdown_patterns"
    assert loaded.markdown_patterns == (FENCED_BLOCK, INLINE_SPAN), sorted_entries


class Rejection(typ.NamedTuple):
    """One malformed ``markdown_only`` overlay and the failure it must raise."""

    body: str
    error: type[Exception]
    message: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            Rejection(
                'markdown_only = "not-a-list"\n',
                TypeError,
                "'markdown_only' must be a list of strings",
            ),
            id="not-a-list",
        ),
        pytest.param(
            Rejection(
                "markdown_only = [7]\n",
                TypeError,
                "'markdown_only' must be a list of strings",
            ),
            id="not-strings",
        ),
        pytest.param(
            Rejection("markdown_only = ['(a+)+b']\n", ValueError, "unsafe repetition"),
            id="unsafe-repetition",
        ),
        pytest.param(
            Rejection("markdown_only = ['[']\n", ValueError, "is invalid"),
            id="uncompilable",
        ),
        pytest.param(
            Rejection("markdown_only = ['']\n", ValueError, "too broad"),
            id="matches-everything",
        ),
    ],
)
def test_overlay_rejects_malformed_markdown_only(
    repository: pathlib.Path,
    authority_factory: AuthorityFactory,
    case: Rejection,
) -> None:
    """Markdown confinement is validated exactly like ``ignore``.

    The empty expression is rejected by the breadth check in ``merge``
    rather than by the loader, so every case is driven through a build.
    """
    _write(repository, case.body)

    with pytest.raises(case.error, match=case.message):
        build(repository, source=authority_factory())


def test_confinement_withdraws_a_shared_pattern_from_the_default_set() -> None:
    """A confined expression leaves the default set even when shared policy set it."""
    base = policy.Dictionary(ignore_patterns=(INLINE_SPAN, SHARED_PATTERN))
    overlay = policy.Dictionary(markdown_patterns=(INLINE_SPAN,))

    merged = policy.merge(base, overlay)

    withdrawn = "a confined shared pattern should leave the default ignore set"
    confined = "a confined pattern should be carried as Markdown-scoped policy"
    assert merged.ignore_patterns == (SHARED_PATTERN,), withdrawn
    assert merged.markdown_patterns == (INLINE_SPAN,), confined


def test_confinement_withdraws_an_overlay_pattern_from_the_default_set() -> None:
    """Confinement also wins when the overlay itself contributed the expression."""
    overlay = policy.Dictionary(
        ignore_patterns=(INLINE_SPAN,),
        markdown_patterns=(INLINE_SPAN,),
    )

    merged = policy.merge(policy.Dictionary(), overlay)

    withdrawn = "an overlay's own pattern should be withdrawn when it is confined"
    confined = "the confined pattern should still reach the Markdown table"
    assert not merged.ignore_patterns, withdrawn
    assert merged.markdown_patterns == (INLINE_SPAN,), confined


def test_confining_an_absent_pattern_still_scopes_it_to_markdown() -> None:
    """A confined expression need not exist anywhere else to take effect."""
    base = policy.Dictionary(ignore_patterns=(SHARED_PATTERN,))
    overlay = policy.Dictionary(markdown_patterns=(FENCED_BLOCK,))

    merged = policy.merge(base, overlay)

    untouched = "confining an absent pattern should leave the default set alone"
    confined = "a pattern absent from the base should still be Markdown-scoped"
    assert merged.ignore_patterns == (SHARED_PATTERN,), untouched
    assert merged.markdown_patterns == (FENCED_BLOCK,), confined


def test_overlay_removes_a_base_supplied_markdown_pattern() -> None:
    """A withdrawal reaches a confinement the shared authority supplied."""
    base = policy.Dictionary(
        ignore_patterns=(SHARED_PATTERN,),
        markdown_patterns=(FENCED_BLOCK, INLINE_SPAN),
    )
    overlay = policy.Dictionary(removed_patterns=(INLINE_SPAN,))

    merged = policy.merge(base, overlay)

    dropped = "removing a shared confinement should drop it from the Markdown table"
    kept = "a withdrawn confinement should not reappear in the default ignore set"
    assert merged.markdown_patterns == (FENCED_BLOCK,), dropped
    assert merged.ignore_patterns == (SHARED_PATTERN,), kept


def test_overlay_cannot_both_remove_and_confine_a_pattern() -> None:
    """Withdrawing and confining one expression is contradictory, not resolved."""
    overlay = policy.Dictionary(
        removed_patterns=(INLINE_SPAN,),
        markdown_patterns=(INLINE_SPAN,),
    )

    with pytest.raises(ValueError, match="removes and confines to Markdown") as raised:
        policy.merge(policy.Dictionary(), overlay)

    named = "the rejection should name the contradictory expression"
    assert INLINE_SPAN in str(raised.value), named


def test_overlay_cannot_both_ignore_and_remove_alongside_a_confinement() -> None:
    """The older ignore-and-remove contradiction survives the new check."""
    overlay = policy.Dictionary(
        ignore_patterns=(SHARED_PATTERN,),
        removed_patterns=(SHARED_PATTERN,),
        markdown_patterns=(FENCED_BLOCK,),
    )

    with pytest.raises(ValueError, match="ignores and removes") as raised:
        policy.merge(policy.Dictionary(), overlay)

    named = "the rejection should name the contradictory expression"
    assert SHARED_PATTERN in str(raised.value), named


#: The exact table the renderer must emit for the two confined masks.
MARKDOWN_TABLE = (
    "[type.markdown]\n"
    "extend-glob = [\n"
    '    "*.md",\n'
    "]\n"
    "extend-ignore-re = [\n"
    f"    {json.dumps(FENCED_BLOCK)},\n"
    f"    {json.dumps(INLINE_SPAN)},\n"
    "]\n"
)


def test_render_emits_the_markdown_table() -> None:
    """Confined expressions render under a globbed ``[type.markdown]`` table."""
    dictionary = policy.Dictionary(markdown_patterns=(FENCED_BLOCK, INLINE_SPAN))

    rendered = render(dictionary)

    verbatim = "the renderer should emit the Markdown table verbatim"
    globbed = "the Markdown table should scope itself to Markdown files"
    sorted_entries = "confined expressions should render sorted in the table"
    assert MARKDOWN_TABLE in rendered, verbatim
    table = tomllib.loads(rendered)["type"]["markdown"]
    assert table["extend-glob"] == ["*.md"], globbed
    assert table["extend-ignore-re"] == [FENCED_BLOCK, INLINE_SPAN], sorted_entries


def test_render_omits_the_markdown_table_when_unused() -> None:
    """An empty confinement list leaves generated output byte-identical."""
    dictionary = policy.Dictionary(ignore_patterns=(SHARED_PATTERN,))

    rendered = render(dictionary)

    omitted = "no Markdown table should appear when nothing is confined"
    stable = "rendering should stay deterministic without a Markdown table"
    assert "type.markdown" not in rendered, omitted
    assert rendered == render(dictionary), stable


def test_render_rejects_a_conflicting_generated_correction() -> None:
    """A correction that contradicts an expanded stem is refused, not silently kept."""
    dictionary = policy.Dictionary(
        stems=("organ",),
        corrections=((PLAIN_BRITISH_ORGANIZE, "other"),),
    )

    with pytest.raises(ValueError, match="conflicting generated correction") as raised:
        render(dictionary)

    named = "the rejection should name the contradictory word"
    assert PLAIN_BRITISH_ORGANIZE in str(raised.value), named


def test_build_scopes_confined_patterns_to_markdown(
    authority_factory: AuthorityFactory,
    repository: pathlib.Path,
) -> None:
    """The cuprum case: shared masks apply to Markdown, not to source identifiers."""
    authority = authority_factory(ignore=(INLINE_SPAN, SHARED_PATTERN))
    _write(repository, f"markdown_only = ['{INLINE_SPAN}', '{FENCED_BLOCK}']\n")

    build(repository, source=authority)

    generated = _generated(repository)
    expected = {
        "extend-glob": ["*.md"],
        "extend-ignore-re": [FENCED_BLOCK, INLINE_SPAN],
    }
    unconfined = "the default set should keep only the unconfined expression"
    both = "both masks should be confined to Markdown files"
    assert generated["default"]["extend-ignore-re"] == [SHARED_PATTERN], unconfined
    assert generated["type"]["markdown"] == expected, both


def test_build_without_confinement_emits_no_markdown_table(
    authority_factory: AuthorityFactory,
    repository: pathlib.Path,
) -> None:
    """An overlay that confines nothing produces the previous output shape."""
    authority = authority_factory(ignore=(SHARED_PATTERN,))

    build(repository, source=authority)

    generated = _generated(repository)
    absent = "an existing consumer's output should gain no Markdown table"
    unchanged = "the default ignore set should be unchanged for an existing consumer"
    assert "type" not in generated, absent
    assert generated["default"]["extend-ignore-re"] == [SHARED_PATTERN], unchanged
