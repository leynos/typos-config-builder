"""Contracts for the policy selection that precedes every phrase read.

The domain query answers one question before it asks the source for text:
does the policy select this tracked path? Policy documents and excluded paths
must be dropped on the path alone, because a read that never happens cannot
fail the scan. These tests pin that ordering at both boundaries: an in-memory
source proves the query issues no read for an unselected path, and a worktree
fixture proves an unreadable excluded path or policy document leaves the scan
intact while an unreadable selected path still fails closed.
"""

from __future__ import annotations

import dataclasses as dc
import pathlib
import typing as typ

import pytest
from conftest import CORRECTION, PROHIBITED, build_repository, cache_text, overlay_text

from typos_config_builder import builder, phrase_source, phrases

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _policy(*, exclude: cabc.Sequence[str] = ()) -> phrases.PhrasePolicy:
    """Return an in-memory policy holding only the prohibited phrase."""
    return phrases.PhrasePolicy(
        phrase_corrections=((PROHIBITED, CORRECTION),),
        ignore_patterns=(),
        excluded_files=tuple(exclude),
    )


def _find_phrases(
    repository: pathlib.Path, policy: phrases.PhrasePolicy
) -> phrases.PhraseScanResult:
    """Run the domain phrase query through the Git worktree adapter."""
    return phrases.find_phrases(phrase_source.RepositoryTextSource(repository), policy)


@dc.dataclass
class _RecordingSource:
    """Serve in-memory records and record every path the query reads."""

    records: tuple[phrases.TrackedInput, ...]
    reads: list[pathlib.Path] = dc.field(default_factory=list)

    def tracked_paths(self) -> cabc.Iterator[pathlib.Path]:
        """Yield every supplied record's path in order."""
        return (record.path for record in self.records)

    def read(self, relative: pathlib.Path) -> phrases.TrackedInput:
        """Record the read and return the supplied record for ``relative``."""
        self.reads.append(relative)
        return next(record for record in self.records if record.path == relative)


def test_policy_documents_and_exclusions_are_never_read() -> None:
    """Selection happens on the path, so unselected inputs are never opened."""
    source = _RecordingSource((
        phrases.TrackedText(pathlib.Path("NOTES.md"), f"Prefer {PROHIBITED}.\n"),
        phrases.TrackedText(
            pathlib.Path(".typos-oxendict-base.toml"), f"{PROHIBITED}\n"
        ),
        phrases.TrackedText(pathlib.Path("vendor/third-party.md"), f"{PROHIBITED}\n"),
    ))

    result = phrases.find_phrases(source, _policy(exclude=("vendor/",)))

    assert source.reads == [pathlib.Path("NOTES.md")], (
        "the query read a policy document or an excluded path"
    )
    assert [finding.path for finding in result.findings] == [
        pathlib.Path("NOTES.md")
    ], "report findings only for selected inputs"
    assert not result.outcomes, "an unselected input contributes no outcome"


def test_excluded_directory_replacement_does_not_fail_the_scan(
    tmp_path: pathlib.Path,
) -> None:
    """An excluded path is unreadable without consequence."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(exclude=(".git", "generated/")),
            "README.md": f"Prefer {CORRECTION}.\n",
            "generated/table.md": f"Prefer {PROHIBITED}.\n",
        },
    )
    (repository / "generated" / "table.md").unlink()
    (repository / "generated" / "table.md").mkdir()

    policy = phrases.load_policy(repository)
    result = _find_phrases(repository, policy)

    assert not result.findings, "an excluded path was scanned"
    assert not result.outcomes, "an excluded path must not report a skip reason"


@pytest.mark.parametrize(
    "document",
    [builder.OVERLAY_NAME, builder.OUTPUT_NAME],
    ids=["overlay", "generated-config"],
)
def test_unreadable_policy_document_does_not_fail_the_scan(
    tmp_path: pathlib.Path, document: str
) -> None:
    """A policy document replaced by a directory is dropped before the read."""
    repository = build_repository(
        tmp_path,
        {
            ".typos-oxendict-base.toml": cache_text(),
            "README.md": f"Prefer {CORRECTION}.\n",
            document: overlay_text(corrections=(("fit-for-purpose", "suitable"),)),
        },
    )
    # Load the policy while its documents are readable, then break one of them:
    # the scan must still succeed because it never opens a policy document.
    policy = phrases.load_policy(repository)
    (repository / document).unlink()
    (repository / document).mkdir()

    result = _find_phrases(repository, policy)

    assert not result.findings, "a policy document was reported as a finding"
    assert not result.outcomes, "a policy document must contribute no outcome"


def test_unreadable_selected_path_still_fails_closed(tmp_path: pathlib.Path) -> None:
    """Selection is the only reason a path escapes the read, not its kind."""
    repository = build_repository(
        tmp_path,
        {".typos-oxendict-base.toml": cache_text(), "README.md": "Readable once.\n"},
    )
    (repository / "README.md").unlink()
    (repository / "README.md").mkdir()

    with pytest.raises(phrases.PhraseScanError) as error:
        _find_phrases(repository, _policy())

    assert error.value.reason is phrases.ScanFailureReason.DIRECTORY_REPLACEMENT, (
        "a selected unreadable path must fail closed"
    )
