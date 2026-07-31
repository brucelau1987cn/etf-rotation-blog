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


def test_futures_page_contains_event_briefing_sections():
    page = (ROOT / "src/pages/futures-compass.astro").read_text(encoding="utf-8")
    briefing = json.loads((ROOT / "public/data/futures-compass-briefing.json").read_text(encoding="utf-8"))
    assert "股指期货交割提示" in page
    assert "标的行业政策利好" in page
    assert "美联储关键信息" in page
    assert "查看金十财经日历" in page
    assert briefing["index_delivery"]["symbols"] == ["IF", "IH", "IC", "IM"]
    assert len(briefing["industry_policy"]) >= 3
    assert len(briefing["fed_watch"]["latest"]) >= 2
    assert 'class="command-grid"' not in page
    assert '<section class="terminal"' in page and '<div class="briefing-grid"' in page
    assert "距交割 {deliveryDays} 天" in page
    assert "briefing-panel+.briefing-panel{border-left:1px" in page


def test_warehouse_fetch_passes_explicit_trade_date_to_exchange_clients(monkeypatch, tmp_path):
    import pandas as pd

    calls = []
    gfex = {
        "LC": pd.DataFrame({"昨日仓单量": [10], "今日仓单量": [12], "增减": [2]}),
        "PS": pd.DataFrame({"昨日仓单量": [20], "今日仓单量": [21], "增减": [1]}),
        "SI": pd.DataFrame({"昨日仓单量": [30], "今日仓单量": [29], "增减": [-1]}),
    }

    class FakeAk:
        @staticmethod
        def futures_gfex_warehouse_receipt(date):
            calls.append(("gfex", date))
            return gfex

        @staticmethod
        def futures_shfe_warehouse_receipt(date):
            calls.append(("shfe", date))
            return {}

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk)
    monkeypatch.setattr(data, "DB_PATH", tmp_path / "futures.db")
    result = data.fetch_warehouse_receipts("20260730")
    assert calls == [("gfex", "20260730"), ("shfe", "20260730")]
    assert result["rows"] == 3
