"""Load, validate, and merge shared spelling policy."""

from __future__ import annotations

import dataclasses as dc
import tomllib
import typing as typ

from typos_config_builder import patterns as pattern_policy

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

SCHEMA_VERSION = 1
REQUIRED_FIELDS = (
    ("oxford", "stems"),
    ("words", "accepted"),
    ("words", "corrections"),
    ("phrases", "corrections"),
    ("patterns", "ignore"),
    ("files", "exclude"),
)


@dc.dataclass(frozen=True, slots=True)
class Dictionary:
    """Represent normalized shared and repository-specific spelling policy.

    Attributes
    ----------
    stems
        Oxford ``-ize`` stems.
    accepted
        Words accepted without replacement.
    corrections
        Word-level source and replacement pairs.
    phrase_corrections
        Phrase-level source and replacement pairs.
    ignore_patterns
        Regular expressions ignored by Typos.
    excluded_files
        File globs excluded from spelling checks.
    """

    stems: tuple[str, ...] = ()
    accepted: tuple[str, ...] = ()
    corrections: tuple[tuple[str, str], ...] = ()
    phrase_corrections: tuple[tuple[str, str], ...] = ()
    ignore_patterns: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = ()


def _table(document: cabc.Mapping[str, object], key: str) -> cabc.Mapping[str, object]:
    """Return one TOML table after validating its shape."""
    value = document.get(key, {})
    if not isinstance(value, dict):
        message = f"{key!r} must be a table"
        raise TypeError(message)
    return typ.cast("cabc.Mapping[str, object]", value)


def _string_list(table: cabc.Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return one normalized string-list field."""
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"{key!r} must be a list of strings"
        raise TypeError(message)
    return tuple(sorted(set(value)))


def _string_mapping(
    table: cabc.Mapping[str, object], key: str, *, description: str
) -> tuple[tuple[str, str], ...]:
    """Return one normalized string-to-string table."""
    value = _table(table, key)
    if not all(
        isinstance(source, str) and isinstance(correction, str)
        for source, correction in value.items()
    ):
        message = f"{description} must map strings to strings"
        raise TypeError(message)
    return tuple(sorted(typ.cast("cabc.Mapping[str, str]", value).items()))


def _is_supported_schema(schema: object) -> bool:
    """Return whether a schema value identifies the supported policy format."""
    is_integer = isinstance(schema, int) and not isinstance(schema, bool)
    return is_integer and schema == SCHEMA_VERSION


def _validate_document(document: cabc.Mapping[str, object], *, sparse: bool) -> None:
    """Validate schema identity and complete-authority fields."""
    schema = document.get("schema")
    if not _is_supported_schema(schema):
        message = f"unsupported dictionary schema {schema!r}"
        raise ValueError(message)
    if sparse:
        return
    for table_name, field_name in REQUIRED_FIELDS:
        if table_name not in document:
            message = f"missing required table {table_name!r}"
            raise ValueError(message)
        table = document[table_name]
        if isinstance(table, dict) and field_name not in table:
            message = f"missing required field {table_name}.{field_name}"
            raise ValueError(message)


def _from_text(text: str, *, sparse: bool) -> Dictionary:
    """Parse normalized spelling policy from TOML text."""
    document = tomllib.loads(text)
    _validate_document(document, sparse=sparse)
    oxford = _table(document, "oxford")
    words = _table(document, "words")
    phrases = _table(document, "phrases")
    patterns = _table(document, "patterns")
    files = _table(document, "files")
    ignore_patterns = _string_list(patterns, "ignore")
    for pattern in ignore_patterns:
        pattern_policy.compile_pattern(pattern)
    return Dictionary(
        stems=_string_list(oxford, "stems"),
        accepted=_string_list(words, "accepted"),
        corrections=_string_mapping(
            words, "corrections", description="word corrections"
        ),
        phrase_corrections=_string_mapping(
            phrases, "corrections", description="phrase corrections"
        ),
        ignore_patterns=ignore_patterns,
        excluded_files=_string_list(files, "exclude"),
    )


def load(path: pathlib.Path, *, sparse: bool = False) -> Dictionary:
    """Load validated policy from a UTF-8 TOML file.

    Parameters
    ----------
    path
        Policy document to load.
    sparse
        Allow omitted sections for a repository overlay when true.

    Returns
    -------
    Dictionary
        Normalized spelling policy.

    Raises
    ------
    OSError
        If the policy document cannot be read.
    ValueError
        If the document is malformed or violates the policy schema.

    Examples
    --------
    >>> dictionary = load(pathlib.Path("typos.local.toml"), sparse=True)
    >>> isinstance(dictionary, Dictionary)
    True
    """
    return _from_text(path.read_text(encoding="utf-8"), sparse=sparse)


def validate_bytes(content: bytes) -> None:
    """Reject bytes that are not a complete shared authority.

    Parameters
    ----------
    content
        UTF-8 TOML bytes to validate.

    Raises
    ------
    UnicodeDecodeError
        If ``content`` is not UTF-8.
    ValueError
        If the document is malformed or violates the policy schema.

    Examples
    --------
    >>> validate_bytes(b"schema = 1")  # doctest: +SKIP
    """
    _from_text(content.decode(), sparse=False)


def _merge_items(
    base: tuple[tuple[str, str], ...],
    local: tuple[tuple[str, str], ...],
    *,
    label: str,
) -> tuple[tuple[str, str], ...]:
    """Merge corrections while rejecting contradictory replacements."""
    merged = dict(base)
    for source, correction in local:
        existing = merged.get(source)
        if existing is not None and existing != correction:
            message = (
                f"conflicting {label} for {source!r}: {existing!r} != {correction!r}"
            )
            raise ValueError(message)
        merged[source] = correction
    return tuple(sorted(merged.items()))


def _validate_local_exceptions(local: Dictionary) -> None:
    """Reject repository exceptions capable of disabling broad checking."""
    pattern_policy.validate_local_exceptions(
        local.ignore_patterns, local.excluded_files
    )


def merge(base: Dictionary, local: Dictionary) -> Dictionary:
    """Merge a shared authority with a sparse, non-conflicting overlay.

    Parameters
    ----------
    base
        Complete shared authority.
    local
        Sparse repository-specific overlay.

    Returns
    -------
    Dictionary
        Deterministically merged policy.

    Raises
    ------
    ValueError
        If the overlay is unsafe or conflicts with the shared authority.

    Examples
    --------
    >>> merged = merge(
    ...     Dictionary(accepted=("Typos",)),
    ...     Dictionary(accepted=("Cyclopts",)),
    ... )
    >>> merged.accepted
    ('Cyclopts', 'Typos')
    """
    _validate_local_exceptions(local)
    return Dictionary(
        stems=tuple(sorted(set(base.stems) | set(local.stems))),
        accepted=tuple(sorted(set(base.accepted) | set(local.accepted))),
        corrections=_merge_items(
            base.corrections, local.corrections, label="correction"
        ),
        phrase_corrections=_merge_items(
            base.phrase_corrections,
            local.phrase_corrections,
            label="phrase correction",
        ),
        ignore_patterns=tuple(
            sorted(set(base.ignore_patterns) | set(local.ignore_patterns))
        ),
        excluded_files=tuple(
            sorted(set(base.excluded_files) | set(local.excluded_files))
        ),
    )
