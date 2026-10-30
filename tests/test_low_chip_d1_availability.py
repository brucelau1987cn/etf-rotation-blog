"""Offline regression coverage for shareholder evidence through D1 serialization."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_low_chip_to_d1 import snapshot_metrics  # noqa: E402


def snapshot(enrichment):
    return {
        "data_as_of": "2026-10-30",
        "intersection": ["600269.SH"],
        "enrichments": {"600269.SH": enrichment},
    }


def test_unknown_shareholders_preserve_null_and_availability_in_wire_payload():
    availability = {"industry": "AVAILABLE", "top10_shareholders": "UNAVAILABLE"}
    rows = snapshot_metrics(snapshot({
        "quality_shareholder": None,
        "institutional_shareholder": None,
        "enrichment_availability": availability,
        "enrichment_status": "degraded",
    }))
    row = json.loads(json.dumps(rows))[0]
    assert row["quality_shareholder"] is None
    assert row["shareholder_nature"] == {
        "report_period": None,
        "quality_shareholder": None,
        "quality_shareholder_names": [],
        "institutional_shareholder": None,
        "institutional_shareholder_names": [],
        "availability": availability,
        "status": "degraded",
    }


@pytest.mark.parametrize("quality", [False, True])
@pytest.mark.parametrize("institutional", [False, True, None])
def test_known_shareholder_flags_retain_false_true_and_independent_null(quality, institutional):
    availability = {"industry": "AVAILABLE", "top10_shareholders": "AVAILABLE"}
    row = snapshot_metrics(snapshot({
        "quality_shareholder": quality,
        "quality_shareholder_names": ["证据股东"] if quality else [],
        "institutional_shareholder": institutional,
        "institutional_shareholder_names": ["证据机构"] if institutional else [],
        "enrichment_availability": availability,
        "enrichment_status": "ok",
    }))[0]
    assert row["quality_shareholder"] == int(quality)
    nature = row["shareholder_nature"]
    assert nature["quality_shareholder"] is quality
    assert nature["institutional_shareholder"] is institutional
    assert nature["availability"] == availability
    assert nature["status"] == "ok"
    assert nature["quality_shareholder_names"] == (["证据股东"] if quality else [])
    assert nature["institutional_shareholder_names"] == (["证据机构"] if institutional else [])


def test_legacy_missing_flags_remain_unknown():
    row = snapshot_metrics(snapshot({"shareholder_metrics": {}}))[0]
    assert row["quality_shareholder"] is None
    assert row["shareholder_nature"]["quality_shareholder"] is None
    assert row["shareholder_nature"]["institutional_shareholder"] is None
