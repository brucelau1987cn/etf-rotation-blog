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
PUBLISH_SPEC = importlib.util.spec_from_file_location("futures_publisher", SCRIPTS / "publish_futures_compass.py")
assert PUBLISH_SPEC and PUBLISH_SPEC.loader
publisher = importlib.util.module_from_spec(PUBLISH_SPEC)
PUBLISH_SPEC.loader.exec_module(publisher)


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
    monkeypatch.setattr(maintenance, "refresh_briefing", lambda: {"status": "ok"})
    monkeypatch.setattr(maintenance, "run_iwencai_review", lambda slot: {"status": "ok", "slot": slot})
    monkeypatch.setattr(maintenance, "fetch_daily_bars", lambda: {"rows": 6})
    monkeypatch.setattr(maintenance, "fetch_warehouse_receipts", lambda: {"rows": 3})
    monkeypatch.setattr(maintenance, "fetch_realtime", lambda: {"generated_at": "2026-07-28T08:30:00+08:00", "count": 9})
    monkeypatch.setattr(maintenance, "validate_public_snapshot", lambda payload: [])
    monkeypatch.setattr(maintenance, "atomic_json", lambda path, payload: writes.append((path, payload)))

    for slot in ("preopen", "day-close", "night"):
        result = maintenance.run_slot(slot)
        assert result["snapshot"]["count"] == 9

    assert len(writes) == 3
    assert all(path == maintenance.PUBLIC_SNAPSHOT for path, _ in writes)


def test_maintenance_exit_fails_when_any_required_stage_errors(monkeypatch, capsys):
    monkeypatch.setattr(maintenance, "slot_calendar_gate", lambda slot: {"status": "run"})
    monkeypatch.setattr(maintenance, "run_slot", lambda slot: {
        "review": {"status": "ok"},
        "briefing": {"status": "error", "detail": "generator failed"},
        "snapshot": {"ok": True, "count": 1, "expected_count": 1, "items": [{"code": "A"}]},
    })
    monkeypatch.setattr(sys, "argv", ["run_futures_compass_maintenance.py", "--slot", "preopen"])

    assert maintenance.main() == 1
    assert json.loads(capsys.readouterr().out)["briefing"]["status"] == "error"


def test_day_close_requires_daily_and_warehouse_stages(monkeypatch):
    result = {
        "review": {"status": "ok"}, "briefing": {"status": "ok"},
        "daily": {"status": "ok"}, "warehouse": {"status": "error"},
        "snapshot": {"ok": True, "count": 1, "expected_count": 1, "items": [{"code": "A"}]},
    }
    monkeypatch.setattr(maintenance, "validate_public_snapshot", lambda payload: [])
    assert maintenance.required_stage_errors("day-close", result) == ["warehouse"]


def test_required_stage_errors_rejects_empty_or_short_snapshot():
    base = {"review": {"status": "ok"}, "briefing": {"status": "ok"}}
    assert maintenance.required_stage_errors("preopen", {
        **base, "snapshot": {"ok": True, "count": 0, "expected_count": 11, "items": []},
    }) == ["snapshot"]
    assert maintenance.required_stage_errors("preopen", {
        **base, "snapshot": {"ok": True, "count": 10, "expected_count": 11, "items": [{"code": "A"}]},
    }) == ["snapshot"]


def test_required_stage_errors_rejects_snapshot_items_count_mismatch():
    result = {
        "review": {"status": "ok"}, "briefing": {"status": "ok"},
        "snapshot": {"ok": True, "count": 2, "expected_count": 2, "items": [{"code": "A"}]},
    }
    assert maintenance.required_stage_errors("preopen", result) == ["snapshot"]


def test_required_stage_errors_uses_complete_snapshot_validator(monkeypatch):
    snapshot = {"ok": True, "count": 1, "expected_count": 1, "items": [{"code": "A"}]}
    calls = []
    monkeypatch.setattr(maintenance, "validate_public_snapshot", lambda payload: calls.append(payload) or ["malformed item"])
    result = {
        "review": {"status": "ok"}, "briefing": {"status": "ok"}, "snapshot": snapshot,
    }

    assert maintenance.required_stage_errors("preopen", result) == ["snapshot"]
    assert calls == [snapshot]


class FakeReviewDB:
    def execute(self, *args): pass
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_iwencai_review_fails_on_legal_empty_result(monkeypatch):
    monkeypatch.setattr(data, "refresh_watchlist", lambda: [{"name": "螺纹钢", "code": "RB"}])
    monkeypatch.setattr(data.subprocess, "run", lambda *args, **kwargs: data.subprocess.CompletedProcess(
        args, 0, stdout=json.dumps({"code_count": 0, "returned_count": 0, "datas": []}), stderr=""
    ))
    class FakeDB:
        def execute(self, *args): pass
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(data, "connect", lambda: FakeDB())
    monkeypatch.setattr(data, "audit", lambda *args, **kwargs: None)

    result = data.run_iwencai_review("preopen")

    assert result["status"] == "error"
    assert "empty" in result["error"]


def test_iwencai_review_fails_on_insufficient_coverage(monkeypatch):
    monkeypatch.setattr(data, "refresh_watchlist", lambda: [
        {"name": "螺纹钢", "code": "RB"}, {"name": "白银", "code": "AG"},
    ])
    monkeypatch.setattr(data.subprocess, "run", lambda *args, **kwargs: data.subprocess.CompletedProcess(
        args, 0, stdout=json.dumps({"code_count": 1, "returned_count": 1, "datas": [{"代码": "RB"}]}), stderr=""
    ))
    class FakeDB:
        def execute(self, *args): pass
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(data, "connect", lambda: FakeDB())
    monkeypatch.setattr(data, "audit", lambda *args, **kwargs: None)

    result = data.run_iwencai_review("preopen")

    assert result["status"] == "error"
    assert "coverage" in result["error"]


def run_review(monkeypatch, watchlist, payload):
    monkeypatch.setattr(data, "refresh_watchlist", lambda: watchlist)
    monkeypatch.setattr(data.subprocess, "run", lambda *args, **kwargs: data.subprocess.CompletedProcess(
        args, 0, stdout=json.dumps(payload), stderr=""
    ))
    monkeypatch.setattr(data, "connect", lambda: FakeReviewDB())
    monkeypatch.setattr(data, "audit", lambda *args, **kwargs: None)
    return data.run_iwencai_review("preopen")


def test_iwencai_review_uses_unique_payload_codes_when_code_count_exceeds_datas(monkeypatch):
    result = run_review(monkeypatch, [
        {"name": "螺纹钢", "code": "RB"}, {"name": "白银", "code": "AG"},
    ], {"code_count": 2, "returned_count": 1, "datas": [{"品种代码": "RB"}]})

    assert result["status"] == "error"
    assert result["covered_codes"] == ["RB"]
    assert result["missing_codes"] == ["AG"]


def test_iwencai_review_deduplicates_rows_by_product_code(monkeypatch):
    result = run_review(monkeypatch, [
        {"name": "螺纹钢", "code": "RB"}, {"name": "白银", "code": "AG"},
    ], {"code_count": 2, "returned_count": 2, "datas": [
        {"品种代码": "RB", "合约代码": "RB2610.SHF"},
        {"品种代码": "RB", "合约代码": "RB2701.SHF"},
    ]})

    assert result["status"] == "error"
    assert result["covered_codes"] == ["RB"]
    assert result["missing_codes"] == ["AG"]


def test_iwencai_review_rejects_row_without_product_code(monkeypatch):
    result = run_review(
        monkeypatch,
        [{"name": "螺纹钢", "code": "RB"}],
        {"code_count": 1, "datas": [{"合约简称": "螺纹钢主力"}]},
    )

    assert result["status"] == "error"
    assert result["invalid_rows"] == 1
    assert result["missing_codes"] == ["RB"]


def test_run_slot_preserves_public_snapshot_when_new_snapshot_is_invalid(monkeypatch):
    writes = []
    malformed = {"ok": True, "count": 1, "expected_count": 1, "items": [{"code": "RB"}]}
    monkeypatch.setattr(maintenance, "run_iwencai_review", lambda slot: {"status": "ok"})
    monkeypatch.setattr(maintenance, "refresh_briefing", lambda: {"status": "ok"})
    monkeypatch.setattr(maintenance, "fetch_realtime", lambda: malformed)
    monkeypatch.setattr(maintenance, "validate_public_snapshot", lambda payload: ["malformed item"])
    monkeypatch.setattr(maintenance, "atomic_json", lambda path, payload: writes.append((path, payload)))

    result = maintenance.run_slot("preopen")

    assert writes == []
    assert result["snapshot_validation_errors"] == ["malformed item"]


def test_run_slot_preserves_public_snapshot_when_review_coverage_is_insufficient(monkeypatch):
    writes = []
    snapshot = {"ok": True, "count": 1, "expected_count": 1, "items": [{"code": "RB"}]}
    monkeypatch.setattr(maintenance, "run_iwencai_review", lambda slot: {
        "status": "error", "error": "iWenCai coverage insufficient: 0/1",
    })
    monkeypatch.setattr(maintenance, "refresh_briefing", lambda: {"status": "ok"})
    monkeypatch.setattr(maintenance, "fetch_realtime", lambda: snapshot)
    monkeypatch.setattr(maintenance, "validate_public_snapshot", lambda payload: [])
    monkeypatch.setattr(maintenance, "atomic_json", lambda path, payload: writes.append((path, payload)))

    result = maintenance.run_slot("preopen")

    assert writes == []
    assert result["snapshot_publication"]["status"] == "blocked"


def test_futures_calendar_gate_uses_current_day_for_day_slots(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "calendar_trading_day", lambda day: calls.append(day) or (True, "fixture"))
    now = datetime(2026, 9, 14, 8, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert maintenance.slot_calendar_gate("preopen", now=now)["status"] == "run"
    assert maintenance.slot_calendar_gate("day-close", now=now)["status"] == "run"
    assert calls == ["2026-09-14", "2026-09-14"]


def calendar_fixture(open_days):
    def lookup(day):
        return day in open_days, "fixture"
    return lookup


def test_futures_night_gate_uses_next_weekday_trading_day(monkeypatch):
    monkeypatch.setattr(
        maintenance,
        "calendar_trading_day",
        calendar_fixture({"2026-09-14", "2026-09-15"}),
    )
    now = datetime(2026, 9, 14, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai"))

    gate = maintenance.slot_calendar_gate("night", now=now)

    assert gate["status"] == "run"
    assert gate["calendar_date"] == "2026-09-15"


def test_futures_friday_night_belongs_to_monday_trading_day(monkeypatch):
    monkeypatch.setattr(
        maintenance,
        "calendar_trading_day",
        calendar_fixture({"2026-09-18", "2026-09-21"}),
    )
    now = datetime(2026, 9, 18, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai"))

    gate = maintenance.slot_calendar_gate("night", now=now)

    assert gate["status"] == "run"
    assert gate["calendar_date"] == "2026-09-21"


def test_futures_night_gate_stops_before_long_holiday(monkeypatch):
    monkeypatch.setattr(
        maintenance,
        "calendar_trading_day",
        calendar_fixture({"2026-09-30", "2026-10-09"}),
    )
    now = datetime(2026, 9, 30, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai"))

    gate = maintenance.slot_calendar_gate("night", now=now)

    assert gate["status"] == "skip"
    assert gate["calendar_date"] == "2026-10-09"


def test_futures_calendar_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(maintenance, "calendar_trading_day", lambda day: (None, "unavailable"))
    now = datetime(2026, 9, 14, 15, 45, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert maintenance.slot_calendar_gate("day-close", now=now)["status"] == "error"


def test_public_snapshot_validation_blocks_old_or_incomplete_payloads(monkeypatch):
    now = datetime(2026, 7, 28, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    codes = ("LC", "PS", "SI", "AU", "AG", "CU", "AL", "SC", "LH", "JM", "SA")
    monkeypatch.setattr(data, "load_watchlist", lambda: [{"code": code} for code in codes])
    fresh = {
        "ok": True,
        "source": "fixture",
        "generated_at": "2026-07-28T08:20:00+08:00",
        "count": 11,
        "expected_count": 11,
        "stale": False,
        "errors": [],
        "summary": {"ranking": list(codes)},
        "items": [valid_item(code) for code in codes],
    }
    assert data.validate_public_snapshot(fresh, now=now) == []

    old = {**fresh, "generated_at": "2026-07-22T00:00:00+08:00"}
    assert any("older than" in error for error in data.validate_public_snapshot(old, now=now))

    incomplete = {**fresh, "count": 5, "items": fresh["items"][:-1]}
    assert any("watchlist" in error for error in data.validate_public_snapshot(incomplete, now=now))

    shell = {
        "ok": True, "source": "fixture", "generated_at": fresh["generated_at"], "count": 11,
        "expected_count": 11, "stale": False, "errors": [], "summary": fresh["summary"],
        "items": [{"code": code} for code in codes],
    }
    assert any("missing core fields" in error for error in data.validate_public_snapshot(shell, now=now))


def test_build_runs_futures_snapshot_freshness_gate():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "scripts/validate_futures_compass.py" in package["scripts"]["build"]


def test_publisher_tracks_briefing_and_deploys_both_json_files():
    source = (SCRIPTS / "publish_futures_compass.py").read_text(encoding="utf-8")
    assert 'BRIEFING = "public/data/futures-compass-briefing.json"' in source
    assert '"https://etf.peekabo.cc/data/futures-compass-briefing.json"' in source
    assert 'with site_publish_lock(), publish_lock()' in source
    assert 'Path(BRIEFING)' in source


def test_futures_page_contains_event_briefing_sections():
    page = (ROOT / "src/pages/futures-compass/index.astro").read_text(encoding="utf-8")
    briefing = json.loads((ROOT / "public/data/futures-compass-briefing.json").read_text(encoding="utf-8"))
    assert "股指期货交割提示" in page
    assert "供需与政策动态" in page
    assert "美联储关键信息" in page
    assert "查看金十财经日历" in page
    assert briefing["index_delivery"]["symbols"] == ["IF", "IH", "IC", "IM"]
    assert len(briefing["industry_policy"]) >= 3
    fed_latest = briefing["fed_watch"]["latest"]
    assert fed_latest
    assert all({"time", "event", "result", "impact"} <= item.keys() for item in fed_latest)
    assert 'class="command-grid"' not in page
    assert '<section class="terminal"' in page and '<div class="briefing-grid"' in page
    assert "距交割 <span class=\"delivery-days\">{deliveryDays}</span> 天" in page
    assert "briefing-panel+.briefing-panel{border-left:1px" in page
    assert "background:linear-gradient(145deg,#fbfcfe 0%,#fff 58%,#f7f3ed 100%)" in page
    assert "background:#17243a" in page


def test_briefing_generator_preserves_last_good_policy_when_upstream_down(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("briefing_gen", SCRIPTS / "generate_futures_compass_briefing.py")
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    out = tmp_path / "futures-compass-briefing.json"
    out.write_text(json.dumps({
        "generated_at": "2026-08-25T15:45:00+08:00",
        "index_delivery": {"date": "2026-09-18", "weekday": "周五", "days_note": "", "symbols": ["IF", "IH", "IC", "IM"]},
        "industry_policy": [
            {"title": "某油田宣布检修减产", "scope": "原油", "as_of": "2026-08-25", "source": "金十数据", "url": "/"},
            {"title": "发改委规划多晶硅产能", "scope": "多晶硅", "as_of": "2026-08-25", "source": "金十数据", "url": "/"},
            {"title": "工信部碳酸锂收储政策", "scope": "碳酸锂", "as_of": "2026-08-25", "source": "金十数据", "url": "/"},
        ],
        "fed_watch": {
            "latest": [{"time": "2026-08-26 20:30 北京时间", "event": "美国初请失业金人数", "result": "等待数据公布", "impact": "金十方向：影响待确认"}],
            "next_focus": "重点跟踪高星级美国通胀、就业、增长数据及美联储决议与官员讲话。",
            "source": "金十数据 API",
            "calendar_url": "https://rili.jin10.com/",
        },
        "data_quality": {"failed": 0, "failures": {}},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(gen, "fetch_calendar_rows", lambda: (_ for _ in ()).throw(RuntimeError("upstream down")))
    monkeypatch.setattr(gen, "fetch_policy_news", lambda: [])
    monkeypatch.setattr(sys, "argv", ["generate_futures_compass_briefing.py", "--output", str(out), "--date", "2026-08-26"])
    assert gen.main() == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["industry_policy"]) == 3, "last-good industry_policy must be preserved when upstream returns empty"
    assert payload["industry_policy"][0]["title"].startswith("某油田")
    assert payload["fed_watch"]["latest"], "last-good fed_watch must be preserved when calendar is down"
    assert payload["data_quality"]["failed"] == 1
    assert "calendar" in payload["data_quality"]["failures"]


def test_each_maintenance_slot_refreshes_event_briefing(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "refresh_briefing", lambda: calls.append("briefing") or {"status": "ok"})
    monkeypatch.setattr(maintenance, "run_iwencai_review", lambda slot: {"status": "ok", "slot": slot})
    monkeypatch.setattr(maintenance, "fetch_daily_bars", lambda: {"rows": 6})
    monkeypatch.setattr(maintenance, "fetch_warehouse_receipts", lambda: {"rows": 3})
    monkeypatch.setattr(maintenance, "fetch_realtime", lambda: {"generated_at": "2026-07-28T08:30:00+08:00", "count": 9})
    monkeypatch.setattr(maintenance, "atomic_json", lambda path, payload: None)

    for slot in ("preopen", "day-close", "night"):
        maintenance.run_slot(slot)

    assert calls == ["briefing", "briefing", "briefing"]


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
