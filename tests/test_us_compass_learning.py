from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/update_us_compass_learning.py"
spec = importlib.util.spec_from_file_location("us_compass_learning", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def snap(day: int, scores=(10, 20, 30), prices=(100, 100, 100), opens=(100, 100, 100)):
    symbols = ("SPY", "AAA", "BBB")
    return {
        "date": f"2026-01-{day:02d}", "exposure": 1.0, "top10": ["BBB", "AAA"],
        "rows": [
            {"symbol": s, "score": score, "adjusted_close": price, "day_open": op}
            for s, score, price, op in zip(symbols, scores, prices, opens)
        ], "outcomes": {},
    }


def test_spearman_and_deviation_mature_perfect_order():
    snapshots = [snap(1), snap(2, prices=(101, 102, 103))]
    mod.mature(snapshots)
    out = snapshots[0]["outcomes"]["t1"]
    assert out["rank_ic"] == 1.0
    assert out["cross_sectional_deviation"] == 0.0


def test_shadow_uses_next_open_and_charges_cost():
    snapshots = [
        snap(1),
        snap(2, opens=(100, 100, 100)),
        snap(3, opens=(110, 110, 110)),
    ]
    shadow = mod.shadow_portfolios(snapshots)
    # 10% gross minus 0.1% initial one-way turnover.
    assert shadow["history"][0]["returns"]["benchmark"] == 0.099
    assert shadow["stats"]["benchmark"]["equity"] == 21980.0


def test_exposure_mapping_and_top10_theme_dedup():
    assert mod.exposure_for("偏强") == 1.0
    assert mod.exposure_for("震荡") == 0.5
    assert mod.exposure_for("防御") == 0.0
    rows = [
        {"symbol": "A", "theme": "科技", "trend_score": 90, "trade_state": "可持有"},
        {"symbol": "B", "theme": "科技", "trend_score": 89, "trade_state": "可持有"},
        {"symbol": "C", "theme": "金融", "trend_score": 80, "trade_state": "观察"},
        {"symbol": "D", "theme": "能源", "trend_score": 99, "trade_state": "退出"},
    ]
    assert mod.choose_top10(rows) == ["A", "C"]
