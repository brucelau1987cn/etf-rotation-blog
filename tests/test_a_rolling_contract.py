from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.a_rolling_contract import TIMEFRAMES, project_upstream, validate_public_payload
from scripts.sync_a_rolling_lkg import sync

ROOT = Path(__file__).resolve().parents[1]
LKG = ROOT / "public/data/a-rolling-signals.json"
SCHEMA = ROOT / "public/schemas/a-rolling-public.schema.json"


def upstream_payload() -> dict:
    fixture = json.loads(LKG.read_text(encoding="utf-8"))
    return {
        "generated_at": "2026-07-23T02:00:00Z",
        "data_as_of": "2026-07-23T01:59:00Z",
        "private_note": "must never be projected",
        "signals": [
            {
                **{key: value for key, value in row.items() if key != "source"},
                "latest_signal_at": "2026-07-23T01:58:00+00:00",
                "internal_id": 999,
                "raw_payload": {"secret": "hidden"},
            }
            for row in fixture["signals"]
        ],
    }


def test_committed_lkg_passes_strict_schema():
    payload = json.loads(LKG.read_text(encoding="utf-8"))
    assert validate_public_payload(payload, SCHEMA) == payload
    assert [row["timeframe"] for row in payload["signals"]] == list(TIMEFRAMES)


def test_projection_allowlists_fields_and_orders_timeframes():
    upstream = upstream_payload()
    upstream["signals"] = list(reversed(upstream["signals"]))
    result = project_upstream(
        upstream,
        generated_at="2026-07-23T02:00:00Z",
        stale_after_seconds=900,
    )
    assert result["mode"] == "live"
    assert result["freshness"] == "fresh"
    assert result["delivery"] == {"state": "live", "reason": None}
    assert [row["timeframe"] for row in result["signals"]] == list(TIMEFRAMES)
    assert all(row["source"] == "UPSTREAM_PROJECTION" for row in result["signals"])
    assert "private_note" not in result
    assert "internal_id" not in result["signals"][0]
    assert "raw_payload" not in result["signals"][0]


@pytest.mark.parametrize("mutation,needle", [
    (lambda payload: payload["signals"].pop(), "incomplete"),
    (lambda payload: payload["signals"].append(copy.deepcopy(payload["signals"][0])), "duplicated"),
    (lambda payload: payload["signals"][0].update(direction="HOLD"), "invalid direction"),
    (lambda payload: payload["signals"][0].pop("phase_code"), "missing fields"),
])
def test_projection_rejects_partial_duplicate_or_invalid_upstream(mutation, needle):
    payload = upstream_payload()
    mutation(payload)
    with pytest.raises(ValueError, match=needle):
        project_upstream(payload, generated_at="2026-07-23T02:00:00Z")


def test_sync_updates_atomically_from_local_source(tmp_path):
    source = tmp_path / "upstream.json"
    output = tmp_path / "lkg.json"
    source.write_text(json.dumps(upstream_payload()), encoding="utf-8")
    result = sync(
        source_file=source,
        output=output,
        now=datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc),
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "updated"
    assert payload["mode"] == "live"
    assert payload["data_as_of"] == "2026-07-23T01:59:00Z"
    assert validate_public_payload(payload) == payload


def test_sync_failure_preserves_existing_lkg(tmp_path):
    source = tmp_path / "upstream.json"
    output = tmp_path / "lkg.json"
    original = LKG.read_bytes()
    output.write_bytes(original)
    broken = upstream_payload()
    broken["signals"].pop()
    source.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError):
        sync(source_file=source, output=output)
    assert output.read_bytes() == original


def test_live_verified_count_cannot_exceed_configured_count():
    payload = json.loads(LKG.read_text(encoding="utf-8"))
    payload["signals"][0]["live_verified_count"] = 19
    with pytest.raises(ValueError, match="exceeds"):
        validate_public_payload(payload)
