from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from etf_bar_cache import connect, get_bars, upsert_bars  # type: ignore[import-not-found]


def load_importer():
    path = ROOT / "scripts" / "update_a_share_bar_cache.py"
    spec = importlib.util.spec_from_file_location("a_share_importer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_cache_prefers_iwencai_qfq(tmp_path):
    db_path = tmp_path / "bars.db"
    base = {"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
            "open": 3.0, "high": 3.1, "low": 2.9, "volume": 10,
            "adjustment": "qfq", "is_final": True}
    with connect(db_path) as db:
        upsert_bars(db, [{**base, "close": 3.01, "source": "stock-api"},
                         {**base, "close": 3.05, "source": "iwencai"}])
        bars = get_bars(db, "XSHG", "510050", "qfq")
    assert len(bars) == 1
    assert bars[0]["source"] == "iwencai"
    assert bars[0]["close"] == 3.05


def test_cache_falls_back_when_preferred_source_has_invalid_ohlc(tmp_path):
    db_path = tmp_path / "bars.db"
    base = {"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
            "volume": 10, "adjustment": "qfq", "is_final": True}
    with connect(db_path) as db:
        upsert_bars(db, [
            {**base, "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "source": "stock-api"},
            {**base, "open": 6.0, "high": 6.1, "low": 5.9, "close": 3.05, "source": "iwencai"},
        ])
        bars = get_bars(db, "XSHG", "510050", "qfq")
    assert len(bars) == 1
    assert bars[0]["source"] == "stock-api"


def test_adjustment_bases_remain_separate(tmp_path):
    db_path = tmp_path / "bars.db"
    base = {"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
            "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05,
            "volume": 10, "source": "tencent", "is_final": True}
    with connect(db_path) as db:
        upsert_bars(db, [{**base, "adjustment": "none"}])
        assert get_bars(db, "XSHG", "510050", "qfq") == []
        assert len(get_bars(db, "XSHG", "510050", "none")) == 1


def test_iwencai_wide_table_is_normalized():
    importer = load_importer()
    payload = {"datas": [{
        "基金代码": "510050.SH",
        "开盘价_前复权[20260710]": 3.0,
        "最高价_前复权[20260710]": 3.1,
        "最低价_前复权[20260710]": 2.9,
        "收盘价_前复权[20260710]": 3.05,
        "成交量[20260710]": 123,
    }]}
    bars, symbols = importer.parse_payload(payload, {"510050": {"code": "510050", "market": "XSHG"}})
    assert symbols == {"510050"}
    assert bars == [{
        "market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
        "adjustment": "qfq", "source": "iwencai", "is_final": True,
        "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "volume": 123.0,
    }]


def test_iwencai_prefixed_fields_are_normalized():
    importer = load_importer()
    payload = {"datas": [{
        "基金代码": "560080.SH",
        "基金@开盘价:前复权[20260713]": "0.936",
        "基金@最高价[20260713]": "0.975",
        "基金@最低价[20260713]": "0.936",
        "基金@收盘价[20260713]": "0.974",
        "基金@成交量[20260713]": "12345",
        "基金@成交额[20260713]": "67890",
    }]}
    bars, symbols = importer.parse_payload(payload, {"560080": {"code": "560080", "market": "XSHG"}})
    assert symbols == {"560080"}
    assert len(bars) == 1
    assert bars[0]["open"] == 0.936
    assert bars[0]["close"] == 0.974
    assert bars[0]["amount"] == 67890.0


def test_iwencai_mixed_adjustment_ohlc_is_rejected():
    importer = load_importer()
    payload = {"datas": [{
        "基金代码": "159667.SZ",
        "基金@开盘价:前复权[20260303]": 1.993,
        "基金@最高价[20260303]": 1.998,
        "基金@最低价[20260303]": 1.896,
        "基金@收盘价:前复权[20260303]": 0.633,
    }]}
    bars, symbols = importer.parse_payload(payload, {"159667": {"code": "159667", "market": "XSHE"}})
    assert bars == []
    assert symbols == set()


def test_stock_api_history_is_normalized_and_finality_is_time_aware():
    importer = load_importer()
    payload = [{"date": "2026-07-15", "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "volume": 123},
               {"date": "2026-07-16", "open": 3.05, "high": 3.2, "low": 3.0, "close": 3.1, "volume": 456}]
    now = datetime(2026, 7, 16, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    bars = importer.parse_stock_api_rows(payload, {"code": "510050", "market": "XSHG"}, now)
    assert len(bars) == 2
    assert bars[0]["source"] == "stock-api" and bars[0]["is_final"] is True
    assert bars[1]["is_final"] is False


def test_tencent_array_shape_is_normalized(monkeypatch):
    importer = load_importer()
    payload = {"data": {"sz159992": {"day": [["2026-07-15", "0.874", "0.912", "0.933", "0.870", "28621018"]]}}}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return __import__("json").dumps(payload).encode()
    monkeypatch.setattr(importer.urllib.request, "urlopen", lambda request, timeout: Response())
    bars = importer.fetch_tencent_history({"code": "159992", "market": "XSHE"}, 320)
    assert len(bars) == 1
    assert bars[0]["source"] == "tencent"
    assert bars[0]["open"] == .874 and bars[0]["close"] == .912


def test_short_history_symbols_are_selected_for_backfill(tmp_path):
    importer = load_importer(); db_path = tmp_path / "bars.db"
    universe = [{"code": "510050", "market": "XSHG"}, {"code": "159915", "market": "XSHE"}]
    with connect(db_path) as db:
        upsert_bars(db, [{"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
                          "close": 3.0, "adjustment": "qfq", "source": "iwencai", "is_final": True}])
        selected = importer.symbols_needing_backfill(db, universe, minimum=2)
    assert [item["code"] for item in selected] == ["510050", "159915"]
