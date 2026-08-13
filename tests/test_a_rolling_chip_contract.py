from pathlib import Path
import importlib.util
from email.message import Message
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


def test_rolling_chip_payload_uses_same_calculator_change_field():
    component = (ROOT / "src/components/ARollingEnergyMatrix.astro").read_text()
    assert "chip.profit_ratio_change_pp" in component
    assert "chip.profit_ratio - chip.yesterday_profit" not in component


def test_chip_refresh_script_reads_v2_endpoint_fields():
    script = (ROOT / "scripts/update_a_rolling_chip.py").read_text()
    assert '"adjust": "qfq"' in script
    assert '"limit": 90' in script
    assert '"profit_ratio_change_pp"' in script
    assert 'latest["profit_ratio_pct"]' in script
    assert 'latest["concentration_90_pct"]' in script
    assert 'latest["average_cost"]' in script


def test_pages_sync_keeps_chip_as_thin_reexport():
    script = (ROOT / "scripts/sync_edge_quote.mjs").read_text()
    assert "chipRouteTarget" in script
    assert "chipHelperTarget" in script
    assert "baostockHelperTarget" in script
    assert "from './_chip.js'" in script
    assert "from './_baostock.js'" in script
    assert "export { onRequestGet, parseSymbol } from './quote.js'" in script
    assert "writeFileSync(chipRouteTarget, text)" not in script


def load_updater():
    path = ROOT / "scripts/update_a_rolling_chip.py"
    spec = importlib.util.spec_from_file_location("update_a_rolling_chip", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chip_refresh_build_payload_executes_v2_mapping(monkeypatch):
    module = load_updater()
    monkeypatch.setattr(module, "load_instruments", lambda: [
        {"symbol": "600021.SH", "instrument_name": "上海电力"},
        {"symbol": "TSLA", "instrument_name": "特斯拉"},
        {"symbol": "01378.HK", "instrument_name": "中国宏桥"},
    ])
    monkeypatch.setattr(module, "fetch_chip", lambda endpoint, symbol: {
        "status": "ok", "adjust": "qfq", "as_of": "2026-08-07",
        "latest": {"profit_ratio_pct": 28.45, "concentration_90_pct": 19.98, "average_cost": 15.96},
        "profit_ratio_change_pp": 5.79,
    })
    payload = module.build_payload("https://example.test/chip")
    assert list(payload["chips"]) == ["600021"]
    assert payload["chips"]["600021"]["profit_ratio_change_pp"] == 5.79


def test_chip_refresh_preserves_partial_success_and_records_failure(monkeypatch):
    module = load_updater()
    monkeypatch.setattr(module, "load_instruments", lambda: [
        {"symbol": "600021.SH", "instrument_name": "上海电力"},
        {"symbol": "688825.SH", "instrument_name": "长鑫科技"},
    ])
    def fake_fetch(endpoint, symbol):
        if symbol == "688825":
            raise HTTPError(endpoint, 502, "bad gateway", Message(), None)
        return {
            "status": "ok", "adjust": "qfq", "as_of": "2026-08-07",
            "latest": {"profit_ratio_pct": 28.45, "concentration_90_pct": 19.98, "average_cost": 15.96},
            "profit_ratio_change_pp": 5.79,
        }
    monkeypatch.setattr(module, "fetch_chip", fake_fetch)
    payload = module.build_payload("https://example.test/chip")
    assert "600021" in payload["chips"]
    assert "688825" in payload["failures"]
