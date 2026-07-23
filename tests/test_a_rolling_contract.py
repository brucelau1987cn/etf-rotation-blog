from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.a_rolling_contract import CYCLES, project_upstream, validate_public_payload
from scripts.sync_a_rolling_lkg import sync

ROOT = Path(__file__).resolve().parents[1]
LKG = ROOT / "public/data/a-rolling-signals.json"
SCHEMA = ROOT / "public/schemas/a-rolling-public.schema.json"


def upstream_payload() -> dict:
    fixture = json.loads(LKG.read_text(encoding="utf-8"))
    return {
        "generated_at": "2026-07-23T04:00:00Z",
        "data_as_of": "2026-07-23T03:59:00Z",
        "private_note": "must never be projected",
        "instrument": fixture["instrument"],
        "cycles": [
            {
                "cycle_code": row["cycle_code"],
                "timeframe_minutes": row["timeframe_minutes"],
                "buy_state": row["buy_state"],
                "buy_triggered_at": "2026-07-23T01:30:00Z",
                "internal_id": index,
            }
            for index, row in enumerate(fixture["cycles"])
        ],
        "sell_alerts": [],
    }


def test_committed_lkg_passes_v2_schema_and_canonical_sequence():
    payload = json.loads(LKG.read_text(encoding="utf-8"))
    assert validate_public_payload(payload, SCHEMA) == payload
    assert payload["schema_version"] == "a-rolling-energy-v2"
    assert len(payload["cycles"]) == 34
    assert [(row["cycle_code"], row["segment"], row["timeframe_minutes"]) for row in payload["cycles"]] == list(CYCLES)
    assert payload["cycles"][22]["cycle_code"] == "E2"
    assert payload["cycles"][22]["timeframe_minutes"] == 370
    assert payload["transmission"]["basis"] == "latest_buy_by_cycle"
    assert payload["transmission"]["continuous_confirmed"] is False
    assert payload["sell_alerts"] == []


def test_projection_allowlists_fields_and_builds_single_run_transmission():
    result = project_upstream(upstream_payload(), generated_at="2026-07-23T04:00:00Z")
    assert result["mode"] == "live"
    assert result["freshness"] == "fresh"
    assert result["delivery"] == {"state": "live", "reason": None}
    assert result["transmission"]["basis"] == "single_run"
    assert result["transmission"]["continuous_confirmed"] is True
    assert result["transmission"]["current_cycle_code"] == "G"
    assert all(row["source"] == "UPSTREAM_PROJECTION" for row in result["cycles"])
    assert "private_note" not in result
    assert "internal_id" not in result["cycles"][0]


@pytest.mark.parametrize("mutation,needle", [
    (lambda payload: payload["cycles"].pop(), "incomplete"),
    (lambda payload: payload["cycles"].append(copy.deepcopy(payload["cycles"][0])), "duplicated"),
    (lambda payload: payload["cycles"][22].update(timeframe_minutes=360), "invalid minutes"),
    (lambda payload: payload["cycles"][4].update(buy_state="INACTIVE", buy_triggered_at=None), "contiguous"),
])
def test_projection_rejects_incomplete_duplicate_wrong_minutes_or_broken_path(mutation, needle):
    payload = upstream_payload()
    mutation(payload)
    with pytest.raises(ValueError, match=needle):
        project_upstream(payload, generated_at="2026-07-23T04:00:00Z")


def test_sync_updates_atomically_from_local_source(tmp_path):
    source = tmp_path / "upstream.json"
    output = tmp_path / "lkg.json"
    source.write_text(json.dumps(upstream_payload()), encoding="utf-8")
    result = sync(source_file=source, output=output, now=datetime(2026, 7, 23, 4, 0, tzinfo=timezone.utc))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "updated"
    assert payload["mode"] == "live"
    assert payload["transmission"]["lit_count"] == 34
    assert validate_public_payload(payload) == payload


def test_sync_failure_preserves_existing_lkg(tmp_path):
    source = tmp_path / "upstream.json"
    output = tmp_path / "lkg.json"
    original = LKG.read_bytes()
    output.write_bytes(original)
    broken = upstream_payload()
    broken["cycles"].pop()
    source.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError):
        sync(source_file=source, output=output)
    assert output.read_bytes() == original


def test_public_payload_rejects_noncontiguous_buy_path():
    payload = json.loads(LKG.read_text(encoding="utf-8"))
    payload["cycles"][5]["buy_state"] = "INACTIVE"
    payload["cycles"][5]["buy_triggered_at"] = None
    payload["transmission"]["lit_count"] = 33
    payload["transmission"]["current_cycle_code"] = "F6"
    with pytest.raises(ValueError, match="canonical|mismatch|contiguous"):
        validate_public_payload(payload)
