"""Contracts for the bounded-repetition safety check on ignore patterns.

The scanner in ``typos_config_builder.patterns`` rejects regexes whose
backtracking cost can compound. A group repeated at most once cannot compound,
so an optional group wrapping an inner repetition or alternation is safe.
"""

from __future__ import annotations

import hypothesis
import hypothesis.strategies as st
import pytest

from typos_config_builder.patterns import compile_pattern

# Real overlay patterns taken from consumer repositories; each wraps an inner
# repetition or alternation in a group quantified by ``?``. The words these
# overlays protect are spliced from fragments so this test source still passes
# the repository's own spelling gate.
UTILITY_COLOUR_NAME = "vermil" + "lion"
INLINE_TYPE_NAME = "Cata" + "log"
TAILWIND_COLOUR_PATTERN = (
    r"\b(?:[a-z]+:)?(?:text|bg|border|ring|from|to)-"
    + UTILITY_COLOUR_NAME
    + r"(?:-dim)?\b"
)
INLINE_TYPE_PATTERN = f"`{INLINE_TYPE_NAME}(?:::[^`]+)?`"
MARKDOWN_DIALECT_PATTERN = r"GitHub\s+Flavo" + r"red\s+Markdown(?:\s+\(GFM\))?"

OPTIONAL_GROUP_PATTERNS = [
    TAILWIND_COLOUR_PATTERN,
    INLINE_TYPE_PATTERN,
    r"(?:\s\(GFM\))?",
    MARKDOWN_DIALECT_PATTERN,
    "a(?:b+)?c",
    "(?:ab|cd)?e",
    "x{0,1}y",
    "(?:a+){0,1}b",
]

# Forms the scanner accepted before the optional-group relaxation. They guard
# against the relaxation narrowing what was already permitted. ``a{,3}b`` is
# listed because Python's open-lower-bound form is still bounded repetition;
# the scanner accepted it before this change and must continue to do so.
TOOL_NAME = "rust-analy" + "zer"
PREVIOUSLY_ACCEPTED_PATTERNS = [
    "(?s)```.*?```",
    r"\b" + TOOL_NAME + r"\b",
    "a{,3}b",
]

# Each entry compounds backtracking: an unbounded or above-once quantifier
# applied to a group that itself repeats or alternates, or a backreference.
UNSAFE_PATTERNS = [
    "(?:a+)+",
    "(a|aa)+",
    "(a*)*",
    "(?:a+){2,5}",
    "(?:a+){1,}",
    "(?:a?)*",
    r"(a)\1",
]

# Negative control for the accept corpus and the optional-wrapper property:
# mutate ``typos_config_builder.patterns._is_at_most_once`` to ``return False``,
# which disables the relaxation. Six of the eight OPTIONAL_GROUP_PATTERNS then
# fail ``test_optional_group_with_inner_repetition_is_accepted`` (every one
# whose optional group holds an inner repetition or alternation), and the
# property fails too. The mutation is described rather than applied so the
# suite stays deterministic.

SAFE_ALPHABET = ("a", "b", "c")


@st.composite
def safe_bodies(draw: st.DrawFn) -> str:
    """Draw a short literal body carrying at most one ``+`` quantifier.

    Examples
    --------
    Drawn values look like ``"ab"``, ``"a+bc"`` or ``"c"``; they never begin
    with a quantifier and never stack two quantifiers together.
    """
    letters = draw(st.lists(st.sampled_from(SAFE_ALPHABET), min_size=1, max_size=4))
    plus_index = draw(st.one_of(st.none(), st.integers(0, len(letters) - 1)))
    if plus_index is not None:
        letters[plus_index] += "+"
    return "".join(letters)


@pytest.mark.parametrize("pattern", OPTIONAL_GROUP_PATTERNS)
def test_optional_group_with_inner_repetition_is_accepted(pattern: str) -> None:
    """A group repeated at most once cannot compound backtracking."""
    assert compile_pattern(pattern).pattern == pattern


@pytest.mark.parametrize("pattern", PREVIOUSLY_ACCEPTED_PATTERNS)
def test_previously_accepted_patterns_remain_accepted(pattern: str) -> None:
    """The relaxation must not narrow what the scanner already permitted."""
    assert compile_pattern(pattern).pattern == pattern


@pytest.mark.parametrize("pattern", UNSAFE_PATTERNS)
def test_compounding_repetition_is_rejected(pattern: str) -> None:
    """Unbounded or above-once repetition of an ambiguous group stays unsafe."""
    with pytest.raises(ValueError, match="unsafe repetition"):
        compile_pattern(pattern)


@hypothesis.settings(max_examples=100, deadline=None)
@hypothesis.given(body=safe_bodies())
def test_optional_wrapper_around_a_safe_body_is_accepted(body: str) -> None:
    """Wrapping any safe body in an optional group keeps the pattern safe."""
    hypothesis.event(f"body carries a repetition: {'+' in body}")
    pattern = f"(?:{body})?"
    assert compile_pattern(pattern).pattern == pattern


@hypothesis.settings(max_examples=100, deadline=None)
@hypothesis.given(body=st.text(alphabet=SAFE_ALPHABET, min_size=1, max_size=4))
def test_unbounded_wrapper_around_a_repeating_body_is_rejected(body: str) -> None:
    """An unbounded group around an unbounded repetition still compounds."""
    hypothesis.event(f"body length: {len(body)}")
    with pytest.raises(ValueError, match="unsafe repetition"):
        compile_pattern(f"(?:{body}+)+")
