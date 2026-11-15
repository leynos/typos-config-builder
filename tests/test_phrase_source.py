"""Unit tests for the phrase-source adapter's category translation.

`RepositoryTextSource` maps every filesystem category onto a domain reason.
The mappings are total for the members that exist today, so the fallbacks
only run when a category reaches the adapter without a mapping: a new enum
member, or a read that produced neither text nor an outcome. Those paths must
fail loudly rather than invent a reason or report a silent success, so they
are exercised here directly instead of through a worktree.
"""

from __future__ import annotations

import typing as typ

import pytest

from typos_config_builder import phrase_source, phrases
from typos_config_builder.phrase_scan_results import (
    ReadOutcome,
    ScanEligibility,
    ScanFailure,
)


def test_skip_reason_rejects_an_input_that_produced_nothing() -> None:
    """A read with neither text nor a category cannot become a skip reason."""
    with pytest.raises(ValueError, match="neither text nor a skip outcome"):
        phrase_source._skip_reason(None)


@pytest.mark.parametrize(
    "category",
    [ScanEligibility.ELIGIBLE, ReadOutcome.TEXT],
    ids=["eligible", "text"],
)
def test_skip_reason_rejects_a_category_with_no_domain_reason(
    category: ScanEligibility | ReadOutcome,
) -> None:
    """A category that means "text was produced" is not a skip reason."""
    with pytest.raises(ValueError, match="unmapped phrase-scan skip outcome"):
        phrase_source._skip_reason(category)


def test_failure_reason_rejects_an_unmapped_failure() -> None:
    """A failure category added without a domain reason fails closed.

    Every current ``ScanFailure`` member is mapped, so the fallback is reached
    with a value the mapping cannot contain. That is exactly the state a new,
    unmapped member would produce.
    """
    unmapped = typ.cast("ScanFailure", object())

    with pytest.raises(ValueError, match="unmapped phrase-scan failure outcome"):
        phrase_source._failure_reason(unmapped)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (ScanFailure.METADATA, phrases.ScanFailureReason.METADATA),
        (ScanFailure.READ, phrases.ScanFailureReason.READ),
        (
            ScanFailure.DIRECTORY_REPLACEMENT,
            phrases.ScanFailureReason.DIRECTORY_REPLACEMENT,
        ),
    ],
)
def test_failure_reason_translates_every_filesystem_failure(
    failure: ScanFailure, reason: phrases.ScanFailureReason
) -> None:
    """Each filesystem failure keeps its own domain reason."""
    assert phrase_source._failure_reason(failure) is reason
