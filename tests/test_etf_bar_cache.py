from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
