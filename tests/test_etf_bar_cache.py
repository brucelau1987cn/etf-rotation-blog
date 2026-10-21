from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from etf_bar_cache import connect, get_bars, upsert_bars  # type: ignore[import-not-found]


@pytest.fixture(autouse=True)
def clear_cf_baostock_environment(monkeypatch):
    monkeypatch.delenv("CF_BAOSTOCK_BASE_URL", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_TOKEN", raising=False)


def load_importer():
    path = ROOT / "scripts" / "update_a_share_bar_cache.py"
    spec = importlib.util.spec_from_file_location("a_share_importer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_cache_prefers_tencent_qfq(tmp_path):
    db_path = tmp_path / "bars.db"
    base = {"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
            "open": 3.0, "high": 3.1, "low": 2.9, "volume": 10,
            "adjustment": "qfq", "is_final": True}
    with connect(db_path) as db:
        upsert_bars(db, [{**base, "close": 3.01, "source": "iwencai"},
                         {**base, "close": 3.05, "source": "tencent"}])
        bars = get_bars(db, "XSHG", "510050", "qfq")
    assert len(bars) == 1
    assert bars[0]["source"] == "tencent"
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


def test_cache_keeps_sources_separate_and_reads_audited_priority(tmp_path):
    db_path = tmp_path / "bars.db"
    base = {"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
            "open": 3.0, "high": 3.1, "low": 2.9, "volume": 10,
            "adjustment": "qfq", "is_final": True}
    with connect(db_path) as db:
        upsert_bars(db, [
            {**base, "close": 3.01, "source": "local-baostock", "fetched_at": "2026-09-15T03:00:00Z"},
            {**base, "close": 3.02, "source": "baostock", "fetched_at": "2026-09-15T02:00:00Z"},
            {**base, "close": 3.03, "source": "cf-baostock", "fetched_at": "2026-09-15T01:00:00Z"},
        ])
        stored = db.execute("SELECT source FROM daily_bars ORDER BY source").fetchall()
        bars = get_bars(db, "XSHG", "510050", "qfq")
    assert [row[0] for row in stored] == ["baostock", "cf-baostock", "local-baostock"]
    assert bars[0]["source"] == "cf-baostock"
    assert bars[0]["close"] == 3.03


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


def test_tencent_business_empty_payload_retries_then_succeeds(monkeypatch):
    importer = load_importer()
    payloads = [
        {"data": ""},
        {"data": {"sz159992": {"qfqday": [["2026-07-15", "0.874", "0.912", "0.933", "0.870", "28621018"]]}}},
    ]
    sleeps = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return __import__("json").dumps(self.payload).encode()

    monkeypatch.setattr(importer.urllib.request, "urlopen", lambda request, timeout: Response(payloads.pop(0)))
    monkeypatch.setattr(importer.time, "sleep", sleeps.append)
    bars = importer.fetch_tencent_history({"code": "159992", "market": "XSHE"}, 320)
    assert len(bars) == 1
    assert sleeps == [1]


def test_fetch_primary_history_uses_cf_baostock_after_tencent(monkeypatch):
    importer = load_importer()
    expected = [{"symbol": "159992", "source": "cf-baostock"}]
    monkeypatch.setattr(importer, "fetch_tencent_history", lambda item, count: (_ for _ in ()).throw(RuntimeError("empty")))
    monkeypatch.setattr(importer, "fetch_cf_baostock_history", lambda item, count: expected)
    monkeypatch.setattr(importer, "fetch_baostock_history", lambda item, count: pytest.fail("local fallback called"))
    rows, source = importer.fetch_primary_history({"code": "159992", "market": "XSHE"}, 5)
    assert rows == expected
    assert source == "cf-baostock"


def test_fetch_primary_history_uses_local_baostock_after_cf_failure(monkeypatch):
    importer = load_importer()
    expected = [{"symbol": "159992", "source": "local-baostock"}]
    monkeypatch.setattr(importer, "fetch_tencent_history", lambda item, count: [])
    monkeypatch.setattr(importer, "fetch_cf_baostock_history", lambda item, count: (_ for _ in ()).throw(RuntimeError("CF down")))
    monkeypatch.setattr(importer, "fetch_baostock_history", lambda item, count: expected)
    rows, source = importer.fetch_primary_history({"code": "159992", "market": "XSHE"}, 5)
    assert rows == expected
    assert source == "local-baostock"


def test_local_baostock_history_uses_qfq_and_audited_source(monkeypatch):
    importer = load_importer()
    captured = {}

    class Result:
        error_code = "0"
        error_msg = ""

        def __init__(self):
            self.rows = iter([["2026-09-14", "sh.510050", "2.8", "2.9", "2.7", "2.85", "100", "285"]])

        def next(self):
            try:
                self.row = next(self.rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self):
            return self.row

    def query(code, fields, **kwargs):
        captured.update({"code": code, "fields": fields, **kwargs})
        return Result()

    fake = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=query,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    rows = importer.fetch_baostock_history({"code": "510050", "market": "XSHG"}, 5)
    assert captured["adjustflag"] == "2"
    assert rows[0]["source"] == "local-baostock"
    assert rows[0]["amount"] == 285.0


def test_cf_baostock_batch_requests_complete_contract_and_maps_rows():
    importer = load_importer()
    items = [
        {"code": "510050", "market": "XSHG"},
        {"code": "159915", "market": "XSHE"},
    ]

    class Client:
        def __init__(self): self.calls = []
        def klines(self, symbols, start, end, *, fields):
            self.calls.append((symbols, start, end, fields))
            return {
                "510050.SH": [{
                        "date": "2026-09-14", "open": "2.80", "high": "2.90",
                        "low": "2.75", "close": "2.88", "volume": "1000",
                        "amount": "2880", "turn": "1.2", "tradestatus": "1",
                    }],
                "159915.SZ": [{
                        "date": "2026-09-14", "open": "1", "high": "1",
                        "low": "1", "close": "1", "volume": "0",
                        "amount": "0", "turn": "0", "tradestatus": "0",
                    }],
            }

    client = Client()
    result = importer.fetch_cf_baostock_batch(
        items, 3, client=client,
        now=datetime(2026, 9, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert len(client.calls) == 1
    # `turn` is deliberately absent from the requested fields: BaoStock reports
    # an empty turnover on a symbol's first trading day, the CF gateway rejects
    # empty numeric fields, and `turn` is not a column in daily_bars anyway.
    assert client.calls[0] == (
        ["510050.SH", "159915.SZ"], "2026-09-09", "2026-09-15",
        ["date", "open", "high", "low", "close", "volume", "amount", "tradestatus"],
    )
    assert result["159915"] == []
    assert result["510050"] == [{
        "market": "XSHG", "symbol": "510050", "trade_date": "2026-09-14",
        "open": 2.8, "high": 2.9, "low": 2.75, "close": 2.88,
        "volume": 1000.0, "amount": 2880.0, "tradestatus": "1",
        "adjustment": "qfq", "source": "cf-baostock", "is_final": True,
    }]


def test_cf_baostock_batch_caps_symbols_at_five_and_fails_closed():
    importer = load_importer()
    items = [{"code": f"{index:06d}", "market": "XSHE"} for index in range(6)]
    with pytest.raises(ValueError, match="at most 5"):
        importer.fetch_cf_baostock_batch(items, 5, client=object())

    class PartialClient:
        def klines(self, symbols, start, end, *, fields):
            return {}

    with pytest.raises(RuntimeError, match="partial"):
        importer.fetch_cf_baostock_batch(items[:1], 5, client=PartialClient())


def test_cf_baostock_rejects_incomplete_or_nonfinite_active_rows():
    importer = load_importer()
    item = {"code": "510050", "market": "XSHG"}

    class Client:
        def __init__(self, row): self.row = row
        def klines(self, symbols, start, end, *, fields):
            return {symbols[0]: [self.row]}

    complete = {
        "date": "2026-09-14", "open": "2.8", "high": "2.9", "low": "2.7",
        "close": "2.85", "volume": "100", "amount": "285",
        "tradestatus": "1",
    }
    # `turn` is no longer requested (BaoStock leaves it empty on a symbol's
    # first trading day and the CF gateway rejects empty numerics), so the
    # non-finite case is exercised on a field the cache does persist.
    for bad in (
        {k: v for k, v in complete.items() if k != "amount"},
        {**complete, "close": "nan"},
    ):
        with pytest.raises(RuntimeError, match="invalid row"):
            importer.fetch_cf_baostock_batch(
                [item], 3, client=Client(bad),
                now=datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
            )


def test_cf_baostock_backfill_splits_at_five_and_repairs_failed_batch(monkeypatch):
    importer = load_importer()
    items = [{"code": f"1599{index:02d}", "market": "XSHE"} for index in range(6)]
    calls = []

    def fetch(batch, count, *, client=None, now=None, db=None):
        calls.append([item["code"] for item in batch])
        if len(batch) > 1:
            raise RuntimeError("batch failed")
        code = batch[0]["code"]
        return {code: [{"symbol": code, "source": "cf-baostock"}]}

    monkeypatch.setattr(importer, "fetch_cf_baostock_batch", fetch)
    bars, succeeded, errors = importer.fetch_cf_baostock_backfill(items, 320, client=object())

    assert calls[:2] == [[item["code"] for item in items[:5]], [items[5]["code"]]]
    assert calls[2:] == [[item["code"]] for item in items[:5]]
    assert succeeded == {item["code"] for item in items}
    assert len(bars) == 6
    assert errors == ["batch 1: RuntimeError: batch failed"]


def test_backfill_uses_local_baostock_for_symbols_cf_cannot_fill(monkeypatch):
    importer = load_importer()
    items = [
        {"code": "510050", "market": "XSHG"},
        {"code": "159915", "market": "XSHE"},
    ]
    cf_rows = [{"symbol": "510050", "source": "cf-baostock"}]
    local_rows = [{"symbol": "159915", "source": "local-baostock"}]
    monkeypatch.setattr(
        importer, "fetch_cf_baostock_backfill",
        lambda items, count, client=None, db=None: (cf_rows, {"510050"}, ["singleton 159915: CF down"]),
    )
    calls = []

    def local(item, count):
        calls.append((item["code"], count))
        return local_rows

    monkeypatch.setattr(importer, "fetch_baostock_history", local)
    bars, succeeded, errors = importer.fetch_baostock_backfill(items, 320, client=object(), workers=1)
    assert bars == cf_rows + local_rows
    assert succeeded == {"510050", "159915"}
    assert calls == [("159915", 320)]
    assert errors == ["singleton 159915: CF down"]


def test_cf_baostock_count_uses_double_calendar_day_window():
    importer = load_importer()
    window = importer._cf_date_range(
        320, datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert window == ("2024-12-14", "2026-09-15")


def test_cf_baostock_rows_preserve_sqlite_bar_contract(tmp_path):
    importer = load_importer()
    item = {"code": "510050", "market": "XSHG"}

    class Client:
        def klines(self, symbols, start, end, *, fields):
            return {symbols[0]: [{
                    "date": "2026-09-14", "open": "2.8", "high": "2.9",
                    "low": "2.7", "close": "2.85", "volume": "100",
                    "amount": "285", "turn": "1", "tradestatus": "1",
                }]}

    bars = importer.fetch_cf_baostock_batch(
        [item], 3, client=Client(),
        now=datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
    )["510050"]
    with connect(tmp_path / "bars.db") as db:
        assert upsert_bars(db, bars) == 1
        stored = dict(db.execute("SELECT * FROM daily_bars").fetchone())
    assert stored["source"] == "cf-baostock"
    assert stored["adjustment"] == "qfq"
    assert stored["amount"] == 285.0
    assert stored["volume"] == 100.0


def test_summarize_source_coverage_counts_final_symbols():
    importer = load_importer()
    bars = [
        {"symbol": "510050", "source": "tencent"},
        {"symbol": "510050", "source": "tencent"},
        {"symbol": "159915", "source": "baostock"},
    ]
    assert importer.summarize_source_coverage(bars) == {"tencent": 1, "baostock": 1}


def test_short_history_symbols_are_selected_for_backfill(tmp_path):
    importer = load_importer(); db_path = tmp_path / "bars.db"
    universe = [{"code": "510050", "market": "XSHG"}, {"code": "159915", "market": "XSHE"}]
    with connect(db_path) as db:
        upsert_bars(db, [{"market": "XSHG", "symbol": "510050", "trade_date": "2026-07-10",
                          "close": 3.0, "adjustment": "qfq", "source": "iwencai", "is_final": True}])
        selected = importer.symbols_needing_backfill(db, universe, minimum=2)
    assert [item["code"] for item in selected] == ["510050", "159915"]


def test_newly_listed_symbol_is_exempt_from_impossible_backfill_minimum(tmp_path):
    """A symbol listed after the minimum window can never satisfy it.

    560710 listed 2026-02-06 and can reach at most ~180 trading days, so a
    260-bar minimum is structurally impossible for it. Selecting it every night
    re-enters the backfill branch forever.
    """
    importer = load_importer(); db_path = tmp_path / "bars.db"
    universe = [{"code": "560710", "market": "XSHG"}, {"code": "600021", "market": "XSHG"}]
    with connect(db_path) as db:
        upsert_bars(db, [
            {"market": "XSHG", "symbol": "560710", "trade_date": "2026-02-06",
             "close": 0.99, "adjustment": "qfq", "source": "tencent", "is_final": True},
            # 600021 has traded for years; its stored history is only a stub.
            {"market": "XSHG", "symbol": "600021", "trade_date": "2015-01-05",
             "close": 19.5, "adjustment": "qfq", "source": "tencent", "is_final": True},
        ])
        selected = importer.symbols_needing_backfill(
            db, universe, minimum=260, today="2026-10-21",
        )
    # 560710 listed 2026-02-06, only ~257 calendar days ago: it cannot reach
    # 260 trading days yet, so requiring a backfill is impossible.
    assert [item["code"] for item in selected] == ["600021"]


def test_backfill_minimum_still_applies_to_established_symbols(tmp_path):
    """A long-listed symbol with too few bars is still selected for backfill."""
    importer = load_importer(); db_path = tmp_path / "bars.db"
    universe = [{"code": "600021", "market": "XSHG"}]
    with connect(db_path) as db:
        upsert_bars(db, [{"market": "XSHG", "symbol": "600021", "trade_date": "2015-01-05",
                          "close": 19.5, "adjustment": "qfq", "source": "tencent", "is_final": True}])
        selected = importer.symbols_needing_backfill(
            db, universe, minimum=260, today="2026-10-21",
        )
    assert [item["code"] for item in selected] == ["600021"]


def test_stale_calendar_does_not_exempt_a_thin_cache_symbol(tmp_path):
    """A calendar that stopped days ago must not mark sparse symbols complete.

    The completeness test compares a symbol's stored bars against the trading
    calendar the cache itself records. When that calendar is stale, the
    comparison would be made against a window that ended weeks ago, so a
    symbol with one stored bar would look "complete" and be exempted from the
    backfill it needs. This is the failure mode that let the nightly chain
    silently stop repairing thin histories.

    A symbol listed only a few days ago is still exempt: it structurally
    cannot reach the minimum (trading days never exceed calendar days).
    """
    importer = load_importer()
    db_path = tmp_path / "bars.db"
    universe = [
        {"code": "600021", "market": "XSHG"},   # long listed, thin cache
        {"code": "560710", "market": "XSHG"},   # listed 5 days ago
    ]
    with connect(db_path) as db:
        upsert_bars(db, [
            {"market": "XSHG", "symbol": "600021", "trade_date": "2015-01-05",
             "close": 19.5, "adjustment": "qfq", "source": "tencent", "is_final": True},
            {"market": "XSHG", "symbol": "560710", "trade_date": "2026-10-16",
             "close": 0.99, "adjustment": "qfq", "source": "tencent", "is_final": True},
        ])
        selected = importer.symbols_needing_backfill(
            db, universe, minimum=260, today="2026-10-21",
        )
    codes = [item["code"] for item in selected]
    assert "600021" in codes, "陈旧日历不能把稀疏标的豁免掉"
    assert "560710" not in codes, "上市仅数日的标的仍然应当豁免"


def test_fresh_calendar_exempts_a_symbol_holding_every_trading_day(tmp_path):
    """With a fresh calendar, a symbol complete for all its days is exempt."""
    importer = load_importer()
    db_path = tmp_path / "bars.db"
    universe = [{"code": "600021", "market": "XSHG"}]
    days = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]
    with connect(db_path) as db:
        upsert_bars(db, [
            {"market": "XSHG", "symbol": "600021", "trade_date": day,
             "close": 19.5, "adjustment": "qfq", "source": "tencent", "is_final": True}
            for day in days
        ])
        selected = importer.symbols_needing_backfill(
            db, universe, minimum=2, today="2026-10-21",
        )
    assert selected == [], "已覆盖全部交易日的标的不应再回填"


def test_cf_window_never_reaches_before_the_first_known_trading_day(tmp_path):
    """The CF gateway 502s on pre-listing windows, so the request must start
    at the first day the symbol actually traded."""
    importer = load_importer(); db_path = tmp_path / "bars.db"
    item = {"code": "560710", "market": "XSHG"}
    with connect(db_path) as db:
        upsert_bars(db, [{"market": "XSHG", "symbol": "560710", "trade_date": "2026-02-06",
                          "close": 0.99, "adjustment": "qfq", "source": "tencent", "is_final": True}])
        start, end = importer._cf_date_range_for(
            320, datetime(2026, 10, 21, tzinfo=ZoneInfo("Asia/Shanghai")), db=db, items=[item],
        )
    assert start == "2026-02-06"
    assert end == "2026-10-21"


def test_cf_window_falls_back_to_calendar_window_without_db_hint(tmp_path):
    importer = load_importer(); db_path = tmp_path / "bars.db"
    item = {"code": "560710", "market": "XSHG"}
    with connect(db_path) as db:
        start, end = importer._cf_date_range_for(
            320, datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai")), db=db, items=[item],
        )
    assert start == "2024-12-14"
    assert end == "2026-09-15"


def test_local_baostock_socket_has_a_finite_timeout(monkeypatch):
    """A blocking socket can hang the whole 480s stage budget on upstream wobble."""
    importer = load_importer()
    captured = {}

    class FakeSocket:
        def settimeout(self, value):
            captured["timeout"] = value

        def gettimeout(self):
            return captured.get("timeout")

    fake_context = types.SimpleNamespace(default_socket=FakeSocket())

    class Result:
        error_code = "0"
        error_msg = ""

        def __init__(self):
            self.rows = iter([["2026-09-14", "sh.510050", "2.8", "2.9", "2.7", "2.85", "100", "285"]])

        def next(self):
            try:
                self.row = next(self.rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self):
            return self.row

    fake = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda *a, **k: Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    monkeypatch.setitem(sys.modules, "baostock.common.context", fake_context)
    rows = importer.fetch_baostock_history({"code": "510050", "market": "XSHG"}, 5)
    assert rows[0]["source"] == "local-baostock"
    assert captured["timeout"] is not None
    assert captured["timeout"] > 0


def test_cache_stage_loads_cf_credentials_from_credential_file(monkeypatch, tmp_path):
    """The cache stage runs with a bare environment, so it must load the
    CF BaoStock credentials itself or the edge gateway is unreachable."""
    importer = load_importer()
    env_file = tmp_path / "baostock-edge.env"
    env_file.write_text(
        "CF_BAOSTOCK_BASE_URL=https://example.test\n"
        "CF_BAOSTOCK_TOKEN=secret-token\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CF_BAOSTOCK_BASE_URL", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_TOKEN", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_DISABLE_CREDENTIAL_FILE", raising=False)
    assert importer.load_cf_credentials(env_file) is True
    assert importer.os.environ["CF_BAOSTOCK_BASE_URL"] == "https://example.test"
    assert importer.os.environ["CF_BAOSTOCK_TOKEN"] == "secret-token"


def test_cache_stage_credential_loader_keeps_unit_tests_offline(monkeypatch, tmp_path):
    importer = load_importer()
    env_file = tmp_path / "baostock-edge.env"
    env_file.write_text("CF_BAOSTOCK_BASE_URL=https://example.test\n", encoding="utf-8")
    monkeypatch.delenv("CF_BAOSTOCK_BASE_URL", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_TOKEN", raising=False)
    monkeypatch.setenv("CF_BAOSTOCK_DISABLE_CREDENTIAL_FILE", "1")
    assert importer.load_cf_credentials(env_file) is False
    assert "CF_BAOSTOCK_BASE_URL" not in importer.os.environ
