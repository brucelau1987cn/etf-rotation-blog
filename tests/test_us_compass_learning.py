from __future__ import annotations

import importlib.util
import json
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


def test_main_writes_identical_model_fingerprint_to_learning_and_shadow(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    learning_path = tmp_path / "learning.json"
    shadow_path = tmp_path / "shadow.json"
    pool_path.write_text(json.dumps({
        "model_date": "2026-01-01",
        "model_version": "us-compass-v1",
        "market_regime": {"state": "震荡"},
        "rows": [
            {
                "symbol": "SPY", "theme": "大盘", "trend_score": 80,
                "trading_risk_score": 10, "trade_state": "可持有",
                "adjusted_close": 100, "day_open": 100, "price": 100,
            },
            {
                "symbol": "QQQ", "theme": "科技", "trend_score": 90,
                "trading_risk_score": 12, "trade_state": "可持有",
                "adjusted_close": 100, "day_open": 100, "price": 100,
            },
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", learning_path)
    monkeypatch.setattr(mod, "SHADOW", shadow_path)

    mod.main()

    learning = json.loads(learning_path.read_text(encoding="utf-8"))
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert learning["model_fingerprint"] == shadow["model_fingerprint"]
    assert learning["model_fingerprint"]["model_version"] == "us-compass-v1"
    assert learning["model_fingerprint"]["universe_count"] == 2
    assert learning["model_fingerprint"]["execution_basis"] == shadow["basis"]
    assert not (tmp_path / "public" / "data").exists()


def test_main_gives_learning_and_shadow_independent_nested_fingerprints(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps({
        "model_date": "2026-01-01", "model_version": "v1",
        "market_regime": {"state": "震荡"},
        "rows": [{"symbol": "SPY", "theme": "大盘", "trend_score": 80,
                  "trading_risk_score": 10, "trade_state": "可持有",
                  "adjusted_close": 100, "day_open": 100, "price": 100}],
    }), encoding="utf-8")
    written = []
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", tmp_path / "learning.json")
    monkeypatch.setattr(mod, "SHADOW", tmp_path / "shadow.json")
    monkeypatch.setattr(mod, "atomic_write", lambda path, payload: written.append(payload))
    mod.main()
    learning_fp = written[0]["model_fingerprint"]
    shadow_fp = written[1]["model_fingerprint"]
    learning_fp["exposure_mapping"]["values"]["偏强"] = 0.25
    assert shadow_fp["exposure_mapping"]["values"]["偏强"] == 1.0
