"""Loading, validation, and merge contracts for shared spelling policy.

The malformed-document matrix and the merge-semantics cases are ported from
the consumer forks, whose vendored rollout scripts enforced the same rules
before the builder owned them.
"""

from __future__ import annotations

import re
import typing as typ

import pytest
from conftest import authority_text

from typos_config_builder import policy

if typ.TYPE_CHECKING:
    import pathlib

# Spliced so this source never contains the prohibited phrase literally.
PROHIBITED = "hand" + "-written"
CORRECTION = "handwritten"
VALID = authority_text()
OVERLAY = 'schema = 1\n\n[words]\naccepted = ["LocalTerm"]\n'


def _write(directory: pathlib.Path, document: str) -> pathlib.Path:
    """Write one policy document and return its path."""
    path = directory / "policy.toml"
    path.write_text(document, encoding="utf-8")
    return path


def test_complete_authority_loads(tmp_path: pathlib.Path) -> None:
    """A complete authority document parses into normalized policy.

    This is the control for the rejection matrix below: a loader that
    refused every document would otherwise satisfy all of it.
    """
    dictionary = policy.load(_write(tmp_path, VALID))

    assert dictionary.stems == ("organ",)
    assert dictionary.accepted == ("oxendict",)


@pytest.mark.parametrize(
    ("document", "error", "message"),
    [
        pytest.param(
            VALID.replace("schema = 1", "schema = 2"),
            ValueError,
            "unsupported dictionary schema 2",
            id="unsupported-schema",
        ),
        pytest.param(
            VALID.replace("schema = 1", "schema = true"),
            ValueError,
            "unsupported dictionary schema True",
            id="boolean-schema",
        ),
        pytest.param(
            VALID.replace("schema = 1", "schema = 1.0"),
            ValueError,
            "unsupported dictionary schema 1.0",
            id="floating-point-schema",
        ),
        pytest.param(
            VALID.replace("[phrases.corrections]\n\n", ""),
            ValueError,
            "missing required table 'phrases'",
            id="missing-table",
        ),
        pytest.param(
            VALID.replace('exclude = [".git"]', ""),
            ValueError,
            "missing required field files.exclude",
            id="missing-field",
        ),
        pytest.param(
            VALID.replace('[oxford]\nstems = ["organ"]', 'oxford = "bad"'),
            TypeError,
            "'oxford' must be a table",
            id="table-is-not-a-table",
        ),
        pytest.param(
            VALID.replace("schema = 1", "schema = 1\nphrases = []").replace(
                "[phrases.corrections]\n\n", ""
            ),
            TypeError,
            "'phrases' must be a table",
            id="phrases-is-not-a-table",
        ),
        pytest.param(
            VALID.replace('stems = ["organ"]', "stems = [1]"),
            TypeError,
            "'stems' must be a list of strings",
            id="string-list-holds-an-integer",
        ),
        pytest.param(
            VALID.replace("ignore = []", 'ignore = "no"'),
            TypeError,
            "'ignore' must be a list of strings",
            id="ignore-is-not-a-list",
        ),
        pytest.param(
            VALID.replace("[words.corrections]", "[words.corrections]\nteh = 1"),
            TypeError,
            "word corrections must map strings to strings",
            id="word-correction-is-not-a-string",
        ),
        pytest.param(
            VALID.replace(
                "[phrases.corrections]",
                f'[phrases.corrections]\n"{PROHIBITED}" = 1',
            ),
            TypeError,
            "phrase corrections must map strings to strings",
            id="phrase-correction-is-not-a-string",
        ),
    ],
)
def test_malformed_policy_is_rejected(
    tmp_path: pathlib.Path,
    document: str,
    error: type[Exception],
    message: str,
) -> None:
    """Schema, table, string-list, and correction shapes stay validated.

    Ported from the ``mriya`` checker matrix and the ``ortho-config`` and
    ``weaver`` dictionary matrices.
    """
    assert document != VALID, "the malformed fixture matched the valid document"

    with pytest.raises(error, match=rf"^{re.escape(message)}$"):
        policy.load(_write(tmp_path, document))


def test_sparse_overlay_carries_only_its_own_policy(tmp_path: pathlib.Path) -> None:
    """A sparse overlay may omit tables without inventing shared policy.

    Ported from the ``ortho-config`` fork.
    """
    overlay = _write(tmp_path, OVERLAY)

    parsed = policy.load(overlay, sparse=True)

    assert parsed.accepted == ("LocalTerm",)
    assert not parsed.stems


def test_sparse_overlay_is_incomplete_as_an_authority(
    tmp_path: pathlib.Path,
) -> None:
    """Only an explicitly sparse load may omit required tables.

    Ported from the ``ortho-config`` fork; the control for the sparse case
    above, which would pass even if sparseness were unconditional.
    """
    overlay = _write(tmp_path, OVERLAY)

    with pytest.raises(ValueError, match="missing required table"):
        policy.load(overlay)


def test_sparse_overlay_corrections_must_be_a_table(
    tmp_path: pathlib.Path,
) -> None:
    """An overlay's corrections table is validated like a shared one.

    Ported from the ``mriya`` fork.
    """
    overlay = _write(tmp_path, "schema = 1\n\n[phrases]\ncorrections = []\n")

    with pytest.raises(TypeError, match="'corrections' must be a table"):
        policy.load(overlay, sparse=True)


SHARED = policy.Dictionary(
    corrections=(("teh", "the"),),
    phrase_corrections=((PROHIBITED, CORRECTION),),
)


def test_empty_overlay_preserves_shared_corrections() -> None:
    """An overlay that says nothing cannot drop a shared correction.

    Ported from the ``weaver`` fork; the control for the conflict cases,
    which a merge that discarded shared policy would also satisfy.
    """
    merged = policy.merge(SHARED, policy.Dictionary())

    assert merged.corrections == SHARED.corrections
    assert merged.phrase_corrections == SHARED.phrase_corrections


@pytest.mark.parametrize(
    ("overlay", "message"),
    [
        pytest.param(
            policy.Dictionary(corrections=(("teh", "ten"),)),
            "conflicting correction for 'teh': 'the' != 'ten'",
            id="word-correction",
        ),
        pytest.param(
            policy.Dictionary(phrase_corrections=((PROHIBITED, "other"),)),
            (
                f"conflicting phrase correction for {PROHIBITED!r}: "
                f"{CORRECTION!r} != 'other'"
            ),
            id="phrase-correction",
        ),
    ],
)
def test_overlay_cannot_contradict_a_shared_correction(
    overlay: policy.Dictionary,
    message: str,
) -> None:
    """A repository overlay cannot silently weaken shared policy.

    Ported from the ``weaver`` and ``ortho-config`` forks.
    """
    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        policy.merge(SHARED, overlay)


def test_merge_unions_stems_and_sorts_every_field() -> None:
    """Merged policy unions overlay vocabulary and stays deterministically sorted.

    Ported from the ``weaver`` and ``ortho-config`` forks, which asserted
    correction ordering; stems and accepted words are included because the
    builder sorts them through the same normalization.
    """
    base = policy.Dictionary(
        stems=("zeta",),
        accepted=("Zulu",),
        corrections=(("zeta", "last"),),
        phrase_corrections=(("zeta phrase", "last phrase"),),
    )
    overlay = policy.Dictionary(
        stems=("alpha",),
        accepted=("Alpha",),
        corrections=(("alpha", "first"),),
        phrase_corrections=(("alpha phrase", "first phrase"),),
    )

    merged = policy.merge(base, overlay)

    assert merged.stems == ("alpha", "zeta")
    assert merged.accepted == ("Alpha", "Zulu")
    assert merged.corrections == (("alpha", "first"), ("zeta", "last"))
    assert merged.phrase_corrections == (
        ("alpha phrase", "first phrase"),
        ("zeta phrase", "last phrase"),
    )
