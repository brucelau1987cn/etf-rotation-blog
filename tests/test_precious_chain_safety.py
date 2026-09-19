import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import update_precious_inventory as collector


def test_status_reflects_mandatory_source_failure():
    payload = collector.build_output(
        {"ok": False}, {"ok": True}, {"ok": True}, {"ok": True}, {"ok": True},
        {"ok": True, "assets": {"gold": {"ok": True}, "silver": {"ok": True}}},
    )
    assert payload["status"] == "error"


def test_atomic_write_keeps_formal_file_when_candidate_is_invalid(tmp_path):
    formal = tmp_path / "precious-inventory.json"
    formal.write_text('{"status":"ok","marker":"old"}', encoding="utf-8")
    candidate = collector.write_output_atomic(
        formal,
        collector.build_output(
            {"ok": False}, {"ok": True}, {"ok": True}, {"ok": True}, {"ok": True},
            {"ok": True, "assets": {"gold": {"ok": True}, "silver": {"ok": True}}},
        ),
        replace=False,
    )
    assert candidate != formal
    assert json.loads(formal.read_text()) == {"status": "ok", "marker": "old"}
    assert json.loads(candidate.read_text())["status"] == "error"


def test_atomic_write_removes_candidate_when_serialization_raises(tmp_path):
    formal = tmp_path / "precious-inventory.json"

    class Broken:
        def __str__(self):
            raise RuntimeError("serialization failed")

    with pytest.raises(RuntimeError, match="serialization failed"):
        collector.write_output_atomic(formal, {"value": Broken()})

    assert list(tmp_path.glob("*.candidate")) == []
    assert list(tmp_path.glob(".*.candidate")) == []
    assert not formal.exists()


def test_atomic_write_removes_candidate_when_replace_raises(tmp_path, monkeypatch):
    formal = tmp_path / "precious-inventory.json"
    monkeypatch.setattr(collector.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))

    with pytest.raises(OSError, match="replace failed"):
        collector.write_output_atomic(formal, {"status": "ok"})

    assert list(tmp_path.glob(".*.candidate")) == []
    assert not formal.exists()


def test_previous_fallback_preserves_as_of_and_rejects_stale_data():
    current = {"ok": False, "as_of": "2026-09-19", "assets": {"gold": {"ok": False}}}
    previous = {"as_of": "2026-09-18", "assets": {"gold": {"ok": True, "symbol": "169_GLD", "day": 1, "week": 2, "month": 3}}}
    merged = collector._merge_previous_etf_profit(
        current, previous, quote_fetcher=lambda _: {"price": 10}, asset_keys=("gold",),
        today=date(2026, 9, 19), max_age_days=2,
    )
    assert merged["assets"]["gold"]["as_of"] == "2026-09-18"

    stale = {"as_of": "2026-09-10", "assets": previous["assets"]}
    rejected = collector._merge_previous_etf_profit(
        {"ok": False, "assets": {"gold": {"ok": False}}}, stale,
        quote_fetcher=lambda _: {"price": 10}, asset_keys=("gold",),
        today=date(2026, 9, 19), max_age_days=2,
    )
    assert rejected["assets"]["gold"]["ok"] is False
