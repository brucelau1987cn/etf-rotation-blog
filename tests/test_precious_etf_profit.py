"""Regression tests for precious ETF quote completeness."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_precious_inventory.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("update_precious_inventory", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_previous_profit_fallback_refreshes_quote_without_changing_profit_ratios():
    current = {
        "ok": False,
        "source": "ths-kline",
        "assets": {"gold": {"ok": False, "error": "ths: no kline"}},
    }
    previous = {
        "assets": {
            "gold": {
                "ok": True,
                "symbol": "169_GLD",
                "name": "GLD黄金ETF",
                "price": 399.72,
                "change_percent": None,
                "day": 41.6,
                "week": 69.15,
                "month": 93.54,
            }
        }
    }

    merged = mod._merge_previous_etf_profit(
        current,
        previous,
        quote_fetcher=lambda symbol: {"price": 404.08, "change_percent": 1.09},
        asset_keys=("gold",),
    )

    row = merged["assets"]["gold"]
    assert row["price"] == 404.08
    assert row["change_percent"] == 1.09
    assert (row["day"], row["week"], row["month"]) == (41.6, 69.15, 93.54)
    assert row["fallback"] == "previous_publish"
    assert previous["assets"]["gold"]["price"] == 399.72
    assert merged["ok"] is True
    assert merged["warnings"] == ["partial_restore_from_previous_publish"]
