"""Render deterministic ``typos.toml`` documents."""

from __future__ import annotations

import json
import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    from typos_config_builder.policy import Dictionary

SUFFIX_PAIRS = (
    ("isably", "izably"),
    ("ise", "ize"),
    ("ises", "izes"),
    ("ised", "ized"),
    ("ising", "izing"),
    ("iser", "izer"),
    ("isers", "izers"),
    ("isable", "izable"),
    ("isation", "ization"),
    ("isations", "izations"),
)


def _word_mappings(dictionary: Dictionary) -> dict[str, str]:
    """Expand Oxford stems and explicit entries into sorted mappings."""
    mappings = {word: word for word in dictionary.accepted}

    def add(word: str, correction: str) -> None:
        """Add one non-conflicting generated word mapping."""
        existing = mappings.get(word)
        if existing is not None and existing != correction:
            message = (
                f"conflicting generated correction for {word!r}: "
                f"{existing!r} != {correction!r}"
            )
            raise ValueError(message)
        mappings[word] = correction

    for word, correction in dictionary.corrections:
        add(word, correction)
    for stem in dictionary.stems:
        for plain_british, oxford in SUFFIX_PAIRS:
            add(f"{stem}{plain_british}", f"{stem}{oxford}")
            add(f"{stem}{oxford}", f"{stem}{oxford}")
    return dict(sorted(mappings.items()))


def _string(value: str) -> str:
    """Quote a string using TOML-compatible JSON syntax."""
    return json.dumps(value, ensure_ascii=False)


def _array(name: str, values: tuple[str, ...]) -> list[str]:
    """Render a deterministic TOML string array."""
    return [f"{name} = [", *(f"    {_string(value)}," for value in values), "]"]


def render(dictionary: Dictionary) -> str:
    r"""Render a parse-checked ``typos.toml`` document with stable ordering.

    Parameters
    ----------
    dictionary
        Normalized spelling policy to render.

    Returns
    -------
    str
        Deterministic TOML ending with a newline.

    Raises
    ------
    ValueError
        If policy entries produce conflicting corrections.

    Examples
    --------
    >>> from typos_config_builder.policy import Dictionary
    >>> "locale = \"en-gb\"" in render(Dictionary())
    True
    """
    lines = [
        "# Generated from the shared en-GB-oxendict dictionary.",
        "# Regenerate with typos-config-builder; do not edit by hand.",
        "",
        "[files]",
        *_array("extend-exclude", dictionary.excluded_files),
        "",
        "[default]",
        'locale = "en-gb"',
        *_array("extend-ignore-re", dictionary.ignore_patterns),
        "",
        "[default.extend-words]",
    ]
    lines.extend(
        f"{_string(word)} = {_string(correction)}"
        for word, correction in _word_mappings(dictionary).items()
    )
    rendered = "\n".join(lines) + "\n"
    tomllib.loads(rendered)
    return rendered
