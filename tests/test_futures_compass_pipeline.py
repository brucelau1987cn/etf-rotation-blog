import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("futures_maintenance", SCRIPTS / "run_futures_compass_maintenance.py")
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)
data = sys.modules["futures_compass_data"]


def valid_item(code):
    return {
        "code": code, "continuous": f"{code}0", "name": code, "exchange": "测试所",
        "contract_code": f"{code}2609", "contract_name": f"{code}2609", "price": 100.0,
        "open": 99.0, "high": 101.0, "low": 98.0, "prev_close": 99.0,
        "volume": 1000.0, "open_interest": 2000.0, "quote_time": "08:20:00",
        "trade_date": "2026-07-28", "source": "fixture", "ma5": 99.0, "ma10": 98.0,
        "ma20": 97.0, "atr14": 2.0, "support": 95.0, "resistance": 105.0,
        "invalidation": 93.0, "trend_state": "多头排列", "structure": "区间震荡",
        "fvg": {"direction": "向上FVG", "lower": 98.0, "upper": 99.0, "status": "未回补"},
        "warehouse_receipt": {"status": "unknown", "trade_date": None, "receipt": None, "change": None},
        "capital_state": "增仓上涨", "signal_label": "趋势跟随",
    }


def test_each_maintenance_slot_refreshes_public_snapshot(monkeypatch):
    writes = []
    monkeypatch.setattr(maintenance, "run_iwencai_review", lambda slot: {"status": "ok", "slot": slot})
    monkeypatch.setattr(maintenance, "fetch_daily_bars", lambda: {"rows": 6})
    monkeypatch.setattr(maintenance, "fetch_warehouse_receipts", lambda: {"rows": 3})
    monkeypatch.setattr(maintenance, "fetch_realtime", lambda: {"generated_at": "2026-07-28T08:30:00+08:00", "count": 9})
    monkeypatch.setattr(maintenance, "atomic_json", lambda path, payload: writes.append((path, payload)))

    for slot in ("preopen", "day-close", "night"):
        result = maintenance.run_slot(slot)
        assert result["snapshot"]["count"] == 9

    assert len(writes) == 3
    assert all(path == maintenance.PUBLIC_SNAPSHOT for path, _ in writes)


def test_public_snapshot_validation_blocks_old_or_incomplete_payloads():
    now = datetime(2026, 7, 28, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    fresh = {
        "ok": True,
        "source": "fixture",
        "generated_at": "2026-07-28T08:20:00+08:00",
        "count": 9,
        "expected_count": 9,
        "stale": False,
        "errors": [],
        "summary": {"ranking": ["LC", "PS", "SI", "AU", "AG", "CU", "AL", "SC", "LH"]},
        "items": [valid_item(code) for code in ("LC", "PS", "SI", "AU", "AG", "CU", "AL", "SC", "LH")],
    }
    assert data.validate_public_snapshot(fresh, now=now) == []

    old = {**fresh, "generated_at": "2026-07-22T00:00:00+08:00"}
    assert any("older than" in error for error in data.validate_public_snapshot(old, now=now))

    incomplete = {**fresh, "count": 5, "items": fresh["items"][:-1]}
    assert any("watchlist" in error for error in data.validate_public_snapshot(incomplete, now=now))

    shell = {
        "ok": True, "source": "fixture", "generated_at": fresh["generated_at"], "count": 9,
        "expected_count": 9, "stale": False, "errors": [], "summary": fresh["summary"],
        "items": [{"code": code} for code in ("LC", "PS", "SI", "AU", "AG", "CU", "AL", "SC", "LH")],
    }
    assert any("missing core fields" in error for error in data.validate_public_snapshot(shell, now=now))


def test_build_runs_futures_snapshot_freshness_gate():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "scripts/validate_futures_compass.py" in package["scripts"]["build"]
