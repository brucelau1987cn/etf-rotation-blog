"""Regression tests for the 2026-10-20 low-chip tracking idempotency defect.

The defect:
- 16:00 main chain runs refresh_low_chip_ftshare_industry.py, which rewrote
  tracking.json's `generated_at` (it refreshes industry fields only — it never
  touches K-line bars).
- 16:40 tracking cron used `generated_at` as its idempotency signal, so it read
  "the file was written today" as "the data covers today" and skipped the
  backfill. The day's bars were never written (tracking's newest bar stayed at
  2026-10-19).

The fix:
- update_low_chip_tracking.py writes `data_through` = the newest bar date it
  actually stored. That is the true coverage date.
- The 16:40 cron reads data_through first, falls back to the max bar date, and
  only then to generated_at.
- The industry refresh writes `industry_refreshed_at` and leaves
  `generated_at` / `data_through` alone.

These are behaviour assertions: each one exercises the real code path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import datetime as _dt
from pathlib import Path

import pytest

from hermes_test_support import local_script_available

ROOT = Path(__file__).resolve().parents[1]
TRACKING_SCRIPT = ROOT / "scripts" / "update_low_chip_tracking.py"
REFRESH_SCRIPT = ROOT / "scripts" / "refresh_low_chip_ftshare_industry.py"
TRACKING_DATA = ROOT / "public" / "data" / "low-chip-tracking.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ============ refresh must not touch the coverage markers ============

def test_industry_refresh_preserves_generated_at_and_data_through(tmp_path, monkeypatch):
    """The industry refresh rewrites industry fields only.

    It must not claim the tracking pool was re-filled today.
    """
    mod = _load(REFRESH_SCRIPT, "refresh_low_chip_industry_probe")

    current = {
        "data_as_of": "2026-10-20",
        "intersection": [],
        "enrichments": {},
    }
    tracking = {
        "schema_version": "low-chip-tracking-v1",
        "generated_at": "2026-10-19T16:50:01+08:00",
        "data_through": "2026-10-19",
        "stocks": {},
    }
    current_path = tmp_path / "current.json"
    tracking_path = tmp_path / "tracking.json"
    cache_path = tmp_path / "cache.json"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    tracking_path.write_text(json.dumps(tracking), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refresh_low_chip_ftshare_industry.py",
            "--current", str(current_path),
            "--tracking", str(tracking_path),
            "--cache", str(cache_path),
        ],
    )
    # No stocks in the pool → nothing to map; the write still must happen.
    mod.main()

    written = json.loads(tracking_path.read_text(encoding="utf-8"))
    assert written["generated_at"] == "2026-10-19T16:50:01+08:00", (
        "industry refresh overwrote generated_at — this is the 2026-10-20 defect"
    )
    assert written["data_through"] == "2026-10-19", (
        "industry refresh overwrote data_through — coverage marker must survive"
    )
    assert written.get("industry_refreshed_at"), "industry refresh must stamp its own marker"


# ============ backfill must publish a truthful coverage date ============

def test_backfill_writes_data_through_matching_newest_bar(tmp_path, monkeypatch):
    """data_through must equal the newest K-line bar actually stored."""
    mod = _load(TRACKING_SCRIPT, "update_low_chip_tracking_probe")

    data_path = tmp_path / "tracking.json"
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "2026-10-20.json").write_text(
        json.dumps({
            "intersection": ["000001.SZ"],
            "enrichments": {"000001.SZ": {"industry": "玻璃玻纤", "financials": {}}},
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(mod, "DATA", data_path)
    # A single active stock with one bar already stored; the backfill adds
    # today's bar through the fake tencent source.
    monkeypatch.setattr(mod, "load_existing", lambda: {"stocks": {}})
    monkeypatch.setattr(mod, "load_history_dates", lambda: {"000001.SZ": ["2026-10-20"]})
    monkeypatch.setattr(
        mod,
        "tencent_daily",
        lambda symbol, start, end: [
            {"date": "2026-10-19", "close": 10.0, "change_pct": None},
            {"date": "2026-10-20", "close": 10.5, "change_pct": 5.0},
        ],
    )
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda symbol: {})
    monkeypatch.setattr(mod, "iwencai_profit_ratio", lambda symbol, date: 1.0)
    monkeypatch.setattr(mod, "fetch_current_year_profit", lambda symbols: {})
    monkeypatch.setattr(mod, "MIN_TRACK_DAYS", 1)
    monkeypatch.setattr(mod, "atomic_write_json", lambda path, payload: Path(path).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    ))

    mod.main()

    written = json.loads(data_path.read_text(encoding="utf-8"))
    assert written["data_through"] == "2026-10-20", (
        f"data_through must be the newest stored bar, got {written.get('data_through')!r}"
    )
    bars = [d["date"] for d in written["stocks"]["000001.SZ"]["daily"]]
    assert max(bars) == written["data_through"]


# ============ the 16:40 cron must key off coverage, not write time ============

def _load_cron():
    cron = Path("/root/.hermes/scripts/update_low_chip_tracking_and_release.py")
    if not local_script_available(cron):
        pytest.skip("host-local tracking cron script not present on this machine")
    return _load(cron, "update_low_chip_tracking_release_probe")


def test_cron_coverage_date_prefers_data_through():
    """data_through wins over a generated_at that claims a later day."""
    mod = _load_cron()
    payload = {
        # The 16:00 industry refresh rewrote generated_at to today …
        "generated_at": "2026-10-20T16:14:18+08:00",
        # … but the pool only covers the previous session.
        "data_through": "2026-10-19",
        "stocks": {"000001.SZ": {"daily": [{"date": "2026-10-19"}]}},
    }
    assert mod.tracking_coverage_date(payload) == _dt.date(2026, 10, 19), (
        "the cron must not treat a rewritten generated_at as coverage"
    )


def test_cron_coverage_date_falls_back_to_newest_bar():
    """Without data_through, the newest stored bar is the coverage date."""
    mod = _load_cron()
    payload = {
        "generated_at": "2026-10-20T16:14:18+08:00",
        "stocks": {
            "000001.SZ": {"daily": [{"date": "2026-10-16"}, {"date": "2026-10-19"}]},
            "000002.SZ": {"daily": [{"date": "2026-10-15"}]},
        },
    }
    assert mod.tracking_coverage_date(payload) == _dt.date(2026, 10, 19)


def test_cron_coverage_date_uses_generated_at_only_as_last_resort():
    mod = _load_cron()
    payload = {"generated_at": "2026-10-20T16:14:18+08:00", "stocks": {}}
    assert mod.tracking_coverage_date(payload) == _dt.date(2026, 10, 20)


def test_cron_coverage_date_returns_none_without_any_signal():
    mod = _load_cron()
    assert mod.tracking_coverage_date({"stocks": {}}) is None
    assert mod.tracking_coverage_date({}, fallback_commit_date="2026-10-18") == _dt.date(2026, 10, 18)


def test_cron_coverage_date_ignores_malformed_markers():
    mod = _load_cron()
    payload = {
        "data_through": "not-a-date",
        "generated_at": "garbage",
        "stocks": {"000001.SZ": {"daily": [{"date": "2026-10-19"}]}},
    }
    assert mod.tracking_coverage_date(payload) == _dt.date(2026, 10, 19)
