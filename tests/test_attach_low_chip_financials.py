"""attach_low_chip_financials.py（Fuyao 源）的单元测试。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "attach_fin", ROOT / "scripts/attach_low_chip_financials.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_helpers():
    import datetime as dt
    from zoneinfo import ZoneInfo
    CN = ZoneInfo("Asia/Shanghai")
    mod = _load()
    assert mod._latest_report(dt.datetime(2026, 9, 3, tzinfo=CN)) == "2026-2"  # 半年报
    assert mod._latest_report(dt.datetime(2026, 5, 3, tzinfo=CN)) == "2026-1"  # 一季报
    assert mod._latest_report(dt.datetime(2026, 11, 3, tzinfo=CN)) == "2026-3"  # 三季报
    assert mod._latest_report(dt.datetime(2026, 2, 3, tzinfo=CN)) == "2025-4"  # 上一年年报
    assert mod._report_to_period("2026-2") == "20260630"
    assert mod._report_to_period("2026-1") == "20260331"
    assert mod._prev_report("2026-2") == "2026-1"
    assert mod._prev_report("2026-1") == "2025-4"


def test_main_writes_financials_from_fuyao(tmp_path, monkeypatch):
    mod = _load()
    data_file = tmp_path / "a-low-chip-stocks.json"
    monkeypatch.setattr(mod, "DATA", data_file)
    data_file.write_text(json.dumps({
        "intersection": ["000001.SZ", "600000.SH"],
        "enrichments": {"000001.SZ": {}, "600000.SH": {}},
    }), encoding="utf-8")

    class FakeClient:
        def financials(self, code, report):
            return {
                "roe": 10.0, "net_margin": 20.0, "gross_margin": 30.0,
                "debt_ratio": 40.0, "cash_profit_ratio": 50.0,
            }

    monkeypatch.setattr(mod, "FuyaoClient", lambda api_key, qps=2.0: FakeClient())
    monkeypatch.setattr(mod, "load_api_key", lambda: "fake-key")
    monkeypatch.setattr(mod, "_latest_report", lambda now: "2026-2")
    monkeypatch.setattr(sys, "argv", ["attach_low_chip_financials.py"])

    mod.main()

    out = json.loads(data_file.read_text(encoding="utf-8"))
    fin = out["enrichments"]["000001.SZ"]["financials"]
    assert fin["roe"] == 10.0
    assert fin["net_margin"] == 20.0
    assert fin["cash_profit_ratio"] == 50.0  # Fuyao 现成，非自算
    assert fin["report_period"] == "20260630"
    assert out["financial_filters"]["report_period"] == "20260630"
    assert set(fin) == {"report_period", "roe", "net_margin", "gross_margin", "debt_ratio", "cash_profit_ratio"}


def test_main_fails_soft_when_no_fuyao_data(tmp_path, monkeypatch):
    mod = _load()
    data_file = tmp_path / "a-low-chip-stocks.json"
    monkeypatch.setattr(mod, "DATA", data_file)
    data_file.write_text(json.dumps({
        "intersection": ["000001.SZ"],
        "enrichments": {"000001.SZ": {}},
    }), encoding="utf-8")

    class EmptyClient:
        def financials(self, code, report):
            return {}

    monkeypatch.setattr(mod, "FuyaoClient", lambda api_key, qps=2.0: EmptyClient())
    monkeypatch.setattr(mod, "load_api_key", lambda: "fake-key")
    monkeypatch.setattr(mod, "_latest_report", lambda now: "2026-2")
    monkeypatch.setattr(sys, "argv", ["attach_low_chip_financials.py"])

    mod.main()  # 不崩溃，fail-soft

    out = json.loads(data_file.read_text(encoding="utf-8"))
    assert out["enrichments"]["000001.SZ"]["financials"] == {}
