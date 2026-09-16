"""Contracts for Markdown-scoped ignore expressions.

Covers the ``[patterns] markdown_only`` overlay key end to end: schema
acceptance and rejection, the merge rule that withholds a confined
expression from the default ignore set, and the rendered
``[type.markdown]`` table.
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

    assert loaded.markdown_patterns == (FENCED_BLOCK, INLINE_SPAN), (
        "markdown_only entries should load sorted into markdown_patterns"
    )


@pytest.mark.parametrize(
    ("body", "error", "message"),
    [
        pytest.param(
            'markdown_only = "not-a-list"\n',
            TypeError,
            "'markdown_only' must be a list of strings",
            id="not-a-list",
        ),
        pytest.param(
            "markdown_only = [7]\n",
            TypeError,
            "'markdown_only' must be a list of strings",
            id="not-strings",
        ),
        pytest.param(
            "markdown_only = ['(a+)+b']\n",
            ValueError,
            "unsafe repetition",
            id="unsafe-repetition",
        ),
        pytest.param(
            "markdown_only = ['[']\n",
            ValueError,
            "is invalid",
            id="uncompilable",
        ),
    ],
)
def test_overlay_rejects_malformed_markdown_only(
    tmp_path: pathlib.Path,
    body: str,
    error: type[Exception],
    message: str,
) -> None:
    """Markdown confinement is validated exactly like ``ignore``."""
    overlay = _write(tmp_path, body)

    with pytest.raises(error, match=message):
        policy.load(overlay, sparse=True)


def test_confinement_withdraws_a_shared_pattern_from_the_default_set() -> None:
    """A confined expression leaves the default set even when shared policy set it."""
    base = policy.Dictionary(ignore_patterns=(INLINE_SPAN, SHARED_PATTERN))
    overlay = policy.Dictionary(markdown_patterns=(INLINE_SPAN,))

    merged = policy.merge(base, overlay)

    assert merged.ignore_patterns == (SHARED_PATTERN,), (
        "a confined shared pattern should not remain in the default ignore set"
    )
    assert merged.markdown_patterns == (INLINE_SPAN,), (
        "a confined pattern should be carried as Markdown-scoped policy"
    )


def test_confinement_withdraws_an_overlay_pattern_from_the_default_set() -> None:
    """Confinement also wins when the overlay itself contributed the expression."""
    overlay = policy.Dictionary(
        ignore_patterns=(INLINE_SPAN,),
        markdown_patterns=(INLINE_SPAN,),
    )

    merged = policy.merge(policy.Dictionary(), overlay)

    assert not merged.ignore_patterns, (
        "an overlay's own pattern should be withdrawn when it is confined"
    )
    assert merged.markdown_patterns == (INLINE_SPAN,), (
        "the confined pattern should still reach the Markdown table"
    )


def test_confining_an_absent_pattern_still_scopes_it_to_markdown() -> None:
    """A confined expression need not exist anywhere else to take effect."""
    base = policy.Dictionary(ignore_patterns=(SHARED_PATTERN,))
    overlay = policy.Dictionary(markdown_patterns=(FENCED_BLOCK,))

    merged = policy.merge(base, overlay)

    assert merged.ignore_patterns == (SHARED_PATTERN,), (
        "confining an absent pattern should leave the default set untouched"
    )
    assert merged.markdown_patterns == (FENCED_BLOCK,), (
        "a pattern absent from the base should still be Markdown-scoped"
    )


def test_overlay_cannot_both_remove_and_confine_a_pattern() -> None:
    """Withdrawing and confining one expression is contradictory, not resolved."""
    overlay = policy.Dictionary(
        removed_patterns=(INLINE_SPAN,),
        markdown_patterns=(INLINE_SPAN,),
    )

    with pytest.raises(ValueError, match="removes and confines to Markdown"):
        policy.merge(policy.Dictionary(), overlay)


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

    assert MARKDOWN_TABLE in rendered, (
        "the renderer should emit the Markdown table verbatim"
    )
    parsed = tomllib.loads(rendered)
    assert parsed["type"]["markdown"]["extend-glob"] == ["*.md"], (
        "the Markdown table should scope itself to Markdown files"
    )
    assert parsed["type"]["markdown"]["extend-ignore-re"] == [
        FENCED_BLOCK,
        INLINE_SPAN,
    ], "confined expressions should render sorted under the Markdown table"


def test_render_omits_the_markdown_table_when_unused() -> None:
    """An empty confinement list leaves generated output byte-identical."""
    dictionary = policy.Dictionary(ignore_patterns=(SHARED_PATTERN,))

    rendered = render(dictionary)

    assert "type.markdown" not in rendered, (
        "no Markdown table should appear when nothing is confined"
    )
    assert rendered == render(policy.Dictionary(ignore_patterns=(SHARED_PATTERN,))), (
        "rendering should stay deterministic without a Markdown table"
    )


def test_build_scopes_confined_patterns_to_markdown(
    authority_factory: AuthorityFactory,
    repository: pathlib.Path,
) -> None:
    """The cuprum case: shared masks apply to Markdown, not to source identifiers."""
    authority = authority_factory(ignore=(INLINE_SPAN, SHARED_PATTERN))
    _write(repository, f"markdown_only = ['{INLINE_SPAN}', '{FENCED_BLOCK}']\n")

    build(repository, source=authority)

    generated = _generated(repository)
    assert generated["default"]["extend-ignore-re"] == [SHARED_PATTERN], (
        "the default ignore set should keep only the unconfined expression"
    )
    assert generated["type"]["markdown"] == {
        "extend-glob": ["*.md"],
        "extend-ignore-re": [FENCED_BLOCK, INLINE_SPAN],
    }, "both masks should be confined to Markdown files"


def test_build_without_confinement_emits_no_markdown_table(
    authority_factory: AuthorityFactory,
    repository: pathlib.Path,
) -> None:
    """An overlay that confines nothing produces the previous output shape."""
    authority = authority_factory(ignore=(SHARED_PATTERN,))

    build(repository, source=authority)

    generated = _generated(repository)
    assert "type" not in generated, (
        "an existing consumer's output should gain no Markdown table"
    )
    assert generated["default"]["extend-ignore-re"] == [SHARED_PATTERN], (
        "the default ignore set should be unchanged for an existing consumer"
    )
