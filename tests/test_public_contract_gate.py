"""The single-market publishers' tolerance contract for the shared validator."""
from __future__ import annotations

import pytest

from scripts.public_contract_gate import (
    catalog_dataset, is_foreign_error, split_errors,
)

A_SHARE_ERRORS = [
    "A-share plant candidates differ from deterministic current-pool rebuild",
    "a-share-research-audit combined fingerprint differs from current inputs or provenance",
    "A research audit as_of 2026-10-29 differs from pool latest 2026-10-30",
    "A action price dates outside plan/baseline batches: allowed=['2026-10-30']",
    "catalog etf-garden-pool bytes mismatch",
    "a-compass-dashboard differs from etf-garden-pool export",
]


@pytest.mark.parametrize("message", A_SHARE_ERRORS)
def test_a_share_drift_is_foreign_for_a_us_publisher(message):
    assert is_foreign_error(message)


@pytest.mark.parametrize("message", [
    "walk_forward.windows: must equal slices length",
    "shadow_health.initial_capital: must equal model_fingerprint.initial_capital",
    "research layer schema: 'health_summary' is a required property",
    "us-compass-research row count differs from summary.universe_count",
])
def test_us_owned_errors_stay_fatal(message):
    assert not is_foreign_error(message)


def test_catalog_error_for_an_owned_dataset_stays_fatal():
    """A stale catalog entry for OUR OWN output means we forgot to regenerate it."""
    owned = frozenset({"us-compass-research", "us-compass-health"})

    assert not is_foreign_error("catalog us-compass-research sha256 mismatch", owned)
    assert is_foreign_error("catalog etf-garden-pool sha256 mismatch", owned)


def test_catalog_dataset_parses_the_key_and_ignores_other_messages():
    assert catalog_dataset("catalog us-compass-research bytes mismatch") == "us-compass-research"
    assert catalog_dataset("catalog inputs contain unrelated changes: ['x']") == "inputs"
    assert catalog_dataset("A-share plant candidates differ") is None


def test_split_errors_keeps_order_and_partitions():
    own, foreign = split_errors(
        A_SHARE_ERRORS + ["walk_forward.windows: must equal slices length"],
    )
    assert own == ["walk_forward.windows: must equal slices length"]
    assert foreign == A_SHARE_ERRORS


def test_split_errors_with_no_errors():
    assert split_errors([]) == ([], [])


def test_tolerance_is_not_a_wildcard():
    """An unknown message must never be tolerated just because the list exists."""
    assert not is_foreign_error("something nobody has classified yet")
