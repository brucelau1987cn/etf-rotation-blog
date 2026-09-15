import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cyq_chip_shadow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cyq_chip_shadow_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_data_as_of_gate_writes_stale_status_and_returns_shadow_exit(tmp_path):
    mod = load_module()
    output = tmp_path / "cyq.json"
    result = mod.write_stale_input(
        output,
        actual="2026-09-11",
        expected="2026-09-14",
        generated_at="2026-09-14T16:45:00+08:00",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 2
    assert payload["status"] == "stale_input"
    assert payload["data_as_of"] == "2026-09-11"
    assert payload["expected_data_as_of"] == "2026-09-14"
    assert payload["production_effect"] == "none"


def test_calendar_gate_skips_closed_day_before_freshness_check():
    mod = load_module()
    assert mod.shadow_calendar_gate("2026-09-13", lookup=lambda day: (False, "fixture")) == {
        "status": "skip", "trade_date": "2026-09-13", "calendar_source": "fixture",
    }
    assert mod.shadow_calendar_gate("2026-09-14", lookup=lambda day: (True, "fixture"))["status"] == "run"


def test_cf_fetch_isolates_each_symbol_request_and_maps_ohlc_turn():
    mod = load_module()

    class Client:
        def __init__(self):
            self.calls = []

        def klines(self, symbols, start, end, *, fields):
            self.calls.append((symbols, start, end, fields))
            return {
                symbol: [
                            {
                                "date": "2026-09-14",
                                "open": "10.0",
                                "high": "11.2",
                                "low": "9.8",
                                "close": "10.5",
                                "turn": "1.25",
                                "tradestatus": "1",
                            },
                            {
                                "date": "2026-09-15",
                                "high": "",
                                "low": "",
                                "close": "",
                                "turn": "",
                                "tradestatus": "0",
                            },
                        ]
                for symbol in symbols
            }

    client = Client()
    codes = [f"{code:06d}.SZ" for code in range(1, 8)]
    frames = mod.fetch_cf_batch(codes, "2026-01-01", "2026-09-15", client=client)

    assert [len(call[0]) for call in client.calls] == [1] * 7
    assert all(call[3] == mod.KLINE_FIELDS for call in client.calls)
    assert all(call[1] == "2026-01-01" and call[2] == "2026-09-15" for call in client.calls)
    assert list(frames) == codes
    assert list(frames[codes[0]].columns) == [
        "date", "open", "high", "low", "close", "turn", "tradestatus",
    ]
    assert len(frames[codes[0]]) == 1
    assert frames[codes[0]].iloc[0]["high"] == 11.2
    assert frames[codes[0]].iloc[0]["turn"] == 1.25


def test_fetch_batch_falls_back_locally_for_failed_cf_chunk_only():
    mod = load_module()
    codes = [f"{code:06d}.SZ" for code in range(1, 8)]

    class Client:
        def klines(self, symbols, start, end, *, fields):
            if symbols == [codes[5]]:
                raise RuntimeError("CF unavailable")
            return {symbol: [] for symbol in symbols}

    fallback_calls = []

    def local_fetch(symbols, start, end):
        fallback_calls.append((symbols, start, end))
        return {symbol: pd.DataFrame([{
            "date": "2026-09-15", "high": 10.0, "low": 9.0,
            "close": 9.5, "turn": 1.0, "tradestatus": "1",
        }]) for symbol in symbols}

    frames = mod.fetch_batch(
        codes, "2026-01-01", "2026-09-15", client=Client(), local_fetch=local_fetch,
    )

    assert fallback_calls == [([codes[5]], "2026-01-01", "2026-09-15")]
    assert frames[codes[0]].empty
    assert len(frames[codes[5]]) == 1


def test_fetch_batch_uses_local_fallback_when_cf_is_unconfigured(monkeypatch):
    mod = load_module()
    codes = ["000001.SZ", "600000.SH"]
    monkeypatch.setattr(
        mod.CFBaoStockClient,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("missing CF config"))),
    )
    calls = []

    def local_fetch(symbols, start, end):
        calls.append((symbols, start, end))
        return {symbol: pd.DataFrame() for symbol in symbols}

    frames = mod.fetch_batch(codes, "2026-01-01", "2026-09-15", local_fetch=local_fetch)

    assert calls == [(codes, "2026-01-01", "2026-09-15")]
    assert list(frames) == codes


def test_fetch_batch_marks_failed_symbols_empty_when_local_fallback_fails():
    mod = load_module()
    codes = ["000001.SZ", "600000.SH"]

    class Client:
        def klines(self, symbols, start, end, *, fields):
            raise RuntimeError("CF unavailable")

    calls = []

    def local_fetch(symbols, start, end):
        calls.append(list(symbols))
        raise RuntimeError("local login blocked")

    frames = mod.fetch_batch(codes, "2026-01-01", "2026-09-15", client=Client(), local_fetch=local_fetch)
    assert calls == [codes]
    assert set(frames) == set(codes)
    assert all(frame.empty for frame in frames.values())


def test_local_fallback_keeps_adjustflag_two_and_ohlc_turn_semantics(monkeypatch):
    mod = load_module()
    captured = {}

    class Result:
        fields = ["date", "open", "high", "low", "close", "turn", "tradestatus"]

        def __init__(self):
            self.rows = iter([
                ["2026-09-14", "10", "11", "9", "10.5", "1.25", "1"],
                ["2026-09-15", "", "", "", "", "", "0"],
            ])

        def next(self):
            try:
                self.current = next(self.rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self):
            return self.current

    def query(code, fields, **kwargs):
        captured.update({"code": code, "fields": fields, **kwargs})
        return Result()

    fake = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=query,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    frames = mod.fetch_local_batch(["600000.SH"], "2026-01-01", "2026-09-15")

    assert captured == {
        "code": "sh.600000",
        "fields": "date,open,high,low,close,turn,tradestatus",
        "start_date": "2026-01-01",
        "end_date": "2026-09-15",
        "frequency": "d",
        "adjustflag": "2",
    }
    assert len(frames["600000.SH"]) == 1
    assert frames["600000.SH"].iloc[0]["open"] == 10.0