from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
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


def test_breakout_shadow_labels_volatility_adjusted_price_and_relative_volume():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + (day % 2), "volume": 100}
        for day in range(1, 21)
    ]
    bars.append({"trade_date": "2026-01-21", "adj_close": 108, "volume": 260})
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.02)
    assert result["status"] == "BREAKOUT"
    assert result["daily_return"] > 0.06
    assert result["relative_volume_10d"] == 2.6
    assert result["volatility_adjusted_move"] >= 2.0
    assert result["relative_spy"] > 0.04
    assert not any(key.startswith("_") for key in result)


def test_breakout_shadow_returns_unavailable_for_incomplete_bars():
    result = mod.breakout_shadow_metric(
        "QQQ",
        [{"trade_date": "2026-01-01", "adj_close": 100, "volume": 10}],
        spy_return=0.0,
    )
    assert result == {
        "symbol": "QQQ",
        "status": "UNAVAILABLE",
        "reason": "requires at least 21 final daily bars with positive prices and 10-day volume history",
    }


def test_breakout_shadow_returns_unavailable_for_zero_historical_volatility():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100, "volume": 100}
        for day in range(1, 21)
    ]
    bars.append({"trade_date": "2026-01-21", "adj_close": 105, "volume": 300})
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0)
    assert result == {
        "symbol": "QQQ", "status": "UNAVAILABLE", "reason": "historical volatility is unavailable",
    }


def test_breakout_shadow_rejects_stale_latest_bar():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + day, "volume": 100}
        for day in range(1, 22)
    ]
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0, expected_trade_date="2026-01-22")
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "latest final bar does not match model_date"


def test_breakout_shadow_uses_adjusted_close_for_return_and_raw_volume_for_ratio():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + (day % 2), "close": 200 + day, "volume": 100}
        for day in range(1, 21)
    ]
    bars.append({"trade_date": "2026-01-21", "adj_close": 108, "close": 999, "volume": 200})
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0)
    assert result["daily_return"] == round(108 / 100 - 1, 6)
    assert result["relative_volume_10d"] == 2.0


def test_breakout_shadow_excludes_current_return_from_volatility_window():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + (day % 2), "volume": 100}
        for day in range(1, 21)
    ]
    low_jump = mod.breakout_shadow_metric("QQQ", bars + [{"trade_date": "2026-01-21", "adj_close": 104, "volume": 200}], spy_return=0.0)
    high_jump = mod.breakout_shadow_metric("QQQ", bars + [{"trade_date": "2026-01-21", "adj_close": 120, "volume": 200}], spy_return=0.0)
    assert low_jump["volatility20"] == high_jump["volatility20"]
    assert high_jump["volatility_adjusted_move"] > low_jump["volatility_adjusted_move"]


def test_breakout_shadow_never_emits_non_finite_numbers():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 1e-308 if day == 20 else 1.0, "volume": 1.0}
        for day in range(1, 21)
    ]
    bars.append({"trade_date": "2026-01-21", "adj_close": 1e308, "volume": 1.0})
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=float("nan"))
    assert result["status"] == "UNAVAILABLE"
    assert all(not isinstance(value, float) or math.isfinite(value) for value in result.values())


def test_breakout_shadow_extreme_finite_history_returns_unavailable():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 1e-308 if day % 2 else 1e308, "volume": 1.0}
        for day in range(1, 22)
    ]
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0)
    assert result["status"] == "UNAVAILABLE"


def test_breakout_shadow_extreme_finite_volume_returns_unavailable_without_private_fields():
    bars = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + (day % 2), "volume": 1e308}
        for day in range(1, 22)
    ]
    result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0)
    assert result["status"] == "UNAVAILABLE"
    assert not any(key.startswith("_") for key in result)


def test_breakout_shadow_oversized_integer_inputs_return_unavailable():
    huge = 10**10000
    base = [
        {"trade_date": f"2026-01-{day:02d}", "adj_close": 100 + (day % 2), "volume": 100}
        for day in range(1, 22)
    ]
    for field in ("adj_close", "volume"):
        bars = [dict(row) for row in base]
        bars[-1][field] = huge
        result = mod.breakout_shadow_metric("QQQ", bars, spy_return=0.0)
        assert result["status"] == "UNAVAILABLE"
        assert not any(key.startswith("_") for key in result)


def test_breakout_report_uses_unrounded_spy_return_at_relative_threshold(tmp_path):
    db = sqlite3.connect(tmp_path / "bars.db")
    db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, adj_close REAL, close REAL, volume REAL, source TEXT, is_final INTEGER)")
    rows = []
    for symbol, final_close in (("SPY", 100.000049), ("QQQ", 101.000025)):
        for day in range(1, 21):
            rows.append((symbol, f"2026-01-{day:02d}", 100 + (day % 2) * 0.01, 100, 100, "yahoo", 1))
        rows.append((symbol, "2026-01-21", final_close, final_close, 300, "yahoo", 1))
    db.executemany("INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)", rows)
    db.commit()
    report = mod.build_breakout_shadow_report(db, model_date="2026-01-21", pool_rows=[{"symbol": "SPY"}, {"symbol": "QQQ"}])
    qqq = next(row for row in report["metrics"] if row["symbol"] == "QQQ")
    assert qqq["relative_spy"] < 0.01
    assert qqq["status"] == "NORMAL"


def test_breakout_shadow_report_is_research_only_and_reads_requested_model_date(tmp_path):
    db = sqlite3.connect(tmp_path / "bars.db")
    db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, adj_close REAL, close REAL, volume REAL, source TEXT, is_final INTEGER)")
    rows = []
    for symbol, jump, volume in [("SPY", 0.02, 120), ("QQQ", 0.08, 260)]:
        for day in range(1, 21):
            rows.append((symbol, f"2026-01-{day:02d}", 100 + (day % 2), 100 + (day % 2), 100, "yahoo", 1))
        rows.append((symbol, "2026-01-21", (101 if 20 % 2 else 100) * (1 + jump), (101 if 20 % 2 else 100) * (1 + jump), volume, "yahoo", 1))
    db.executemany("INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)", rows)
    db.commit()

    report = mod.build_breakout_shadow_report(
        db,
        model_date="2026-01-21",
        pool_rows=[{"symbol": "SPY", "theme": "大盘"}, {"symbol": "QQQ", "theme": "科技"}],
    )
    assert report["mode"] == "shadow_research_only"
    assert report["production_change_allowed"] is False
    assert report["model_date"] == "2026-01-21"
    assert report["coverage"] == {"requested": 2, "evaluated": 2, "unavailable": 0}
    assert [row["symbol"] for row in report["hits"]] == ["QQQ"]


def test_breakout_shadow_report_fails_relative_strength_closed_when_spy_is_unavailable(tmp_path):
    db = sqlite3.connect(tmp_path / "bars.db")
    db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, adj_close REAL, close REAL, volume REAL, source TEXT, is_final INTEGER)")
    db.executemany(
        "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)",
        [("QQQ", f"2026-01-{day:02d}", 100 + day, 100 + day, 100, "yahoo", 1) for day in range(1, 22)],
    )
    db.commit()
    report = mod.build_breakout_shadow_report(
        db, model_date="2026-01-21", pool_rows=[{"symbol": "SPY"}, {"symbol": "QQQ"}],
    )
    assert report["status"] == "UNAVAILABLE"
    assert report["reason"] == "SPY benchmark return is unavailable for model_date"
    assert report["coverage"] == {"requested": 2, "evaluated": 0, "unavailable": 2}
    assert report["hits"] == []


def test_append_breakout_history_is_idempotent_and_keeps_latest_520_dates():
    old = [{"model_date": f"2024-01-{index:03d}", "hits": []} for index in range(1, 522)]
    report = {"model_date": "2026-01-21", "coverage": {"requested": 2, "evaluated": 2, "unavailable": 0}, "hits": [{"symbol": "QQQ"}]}
    history = mod.append_breakout_history(old, report)
    assert len(history) == 520
    assert history[-1] == {
        "model_date": "2026-01-21",
        "coverage": report["coverage"],
        "hits": report["hits"],
    }
    replaced = mod.append_breakout_history(history, {**report, "hits": []})
    assert len(replaced) == 520
    assert replaced[-1]["hits"] == []


def test_append_breakout_history_drops_malformed_and_duplicate_dates():
    history = [
        {"model_date": None, "hits": []},
        {"model_date": "2026-01-01", "hits": [{"symbol": "OLD"}]},
        {"model_date": "2026-01-01", "hits": [{"symbol": "NEW"}]},
        "bad",
    ]
    rows = mod.append_breakout_history(history, {"model_date": "2026-01-02", "coverage": {}, "hits": []})
    assert [row["model_date"] for row in rows] == ["2026-01-01", "2026-01-02"]
    assert rows[0]["hits"] == [{"symbol": "NEW"}]


def test_rotation_shadow_metric_uses_21_and_63_session_momentum():
    bars = [
        {"trade_date": f"2026-03-{index:02d}", "adj_close": 100 + index}
        for index in range(1, 65)
    ]
    result = mod.rotation_shadow_metric("QQQ", bars, expected_trade_date="2026-03-64")
    short_return = 164 / 143 - 1
    long_return = 164 / 101 - 1
    expected_score = (100 * short_return + 75 * long_return) / 175
    assert result == {
        "symbol": "QQQ",
        "status": "ELIGIBLE",
        "trade_date": "2026-03-64",
        "return_21d": round(short_return, 8),
        "return_63d": round(long_return, 8),
        "rotation_score": round(expected_score, 8),
    }


def test_rotation_shadow_metric_fails_closed_for_stale_or_incomplete_bars():
    incomplete = mod.rotation_shadow_metric(
        "QQQ", [{"trade_date": "2026-01-01", "adj_close": 100}], expected_trade_date="2026-01-01",
    )
    assert incomplete["status"] == "UNAVAILABLE"
    assert "64 final daily bars" in incomplete["reason"]
    bars = [
        {"trade_date": f"2026-03-{index:02d}", "adj_close": 100 + index}
        for index in range(1, 65)
    ]
    stale = mod.rotation_shadow_metric("QQQ", bars, expected_trade_date="2026-04-01")
    assert stale == {
        "symbol": "QQQ", "status": "UNAVAILABLE", "reason": "latest final bar does not match model_date",
    }


def test_rotation_shadow_metric_requires_adjusted_close():
    bars = [
        {"trade_date": f"2026-03-{index:02d}", "close": 100 + index}
        for index in range(1, 65)
    ]
    result = mod.rotation_shadow_metric("QQQ", bars, expected_trade_date="2026-03-64")
    assert result == {
        "symbol": "QQQ", "status": "UNAVAILABLE", "reason": mod.ROTATION_UNAVAILABLE_REASON,
    }


def test_rotation_shadow_report_deduplicates_themes_and_is_research_only(tmp_path):
    db = sqlite3.connect(tmp_path / "bars.db")
    db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, adj_close REAL, source TEXT, is_final INTEGER)")
    rows = []
    for symbol, slope in (("QQQ", 1.0), ("XLK", 0.8), ("XLE", 0.6), ("SGOV", 0.01)):
        for index in range(1, 65):
            rows.append((symbol, f"2026-03-{index:02d}", 100 + slope * index, "yahoo", 1))
    db.executemany("INSERT INTO daily_bars VALUES(?,?,?,?,?)", rows)
    db.commit()
    report = mod.build_rotation_shadow_report(
        db,
        model_date="2026-03-64",
        pool_rows=[
            {"symbol": "QQQ", "theme": "科技"},
            {"symbol": "XLK", "theme": "科技"},
            {"symbol": "XLE", "theme": "能源"},
            {"symbol": "SGOV", "theme": "现金"},
        ],
    )
    assert report["mode"] == "shadow_research_only"
    assert report["production_change_allowed"] is False
    assert report["production_weights_changed"] is False
    assert report["signal"] == "RISK_ON_OBSERVATION"
    assert [row["symbol"] for row in report["selection"]] == ["QQQ", "XLE"]
    assert report["coverage"] == {"requested": 4, "evaluated": 4, "unavailable": 0}
    assert report["observation_gate"] == {
        "minimum_completed_days": 10,
        "preferred_completed_days": 20,
        "completed_days": 0,
        "status": "ACCUMULATING",
    }


def test_rotation_shadow_report_marks_zero_evaluated_coverage_unavailable(tmp_path):
    db = sqlite3.connect(tmp_path / "bars.db")
    db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, adj_close REAL, source TEXT, is_final INTEGER)")
    report = mod.build_rotation_shadow_report(
        db, model_date="2026-03-64", pool_rows=[{"symbol": "QQQ", "theme": "科技"}],
    )
    assert report["status"] == "UNAVAILABLE"
    assert report["signal"] is None
    assert report["reason"] == "no ETF has 64 valid final adjusted-close bars for model_date"
    assert report["observation_gate"]["status"] == "UNAVAILABLE"


def test_append_rotation_history_skips_unavailable_report():
    old = [{"model_date": "2026-03-63", "selection": [{"symbol": "QQQ"}]}]
    report = {
        "model_date": "2026-03-64", "status": "UNAVAILABLE", "signal": None,
        "coverage": {"requested": 1, "evaluated": 0, "unavailable": 1},
        "selection": [], "observation_gate": {},
    }
    assert mod.append_rotation_history(old, report) == old
    assert report["observation_gate"]["completed_days"] == 1
    assert report["observation_gate"]["status"] == "UNAVAILABLE"


def test_append_rotation_history_is_idempotent_and_updates_observation_gate():
    report = {
        "model_date": "2026-03-64",
        "signal": "RISK_ON_OBSERVATION",
        "coverage": {"requested": 2, "evaluated": 2, "unavailable": 0},
        "selection": [{"symbol": "QQQ", "rotation_score": 0.2}],
        "observation_gate": {},
    }
    history = mod.append_rotation_history(
        [{"model_date": f"2026-01-{day:02d}", "selection": []} for day in range(1, 10)], report,
    )
    assert len(history) == 10
    assert report["observation_gate"]["completed_days"] == 10
    assert report["observation_gate"]["status"] == "MINIMUM_REACHED"
    replaced = mod.append_rotation_history(history, {**report, "selection": []})
    assert len(replaced) == 10
    assert replaced[-1]["selection"] == []


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
    monkeypatch.setattr(mod, "BAR_DB", tmp_path / "missing-bars.db")

    mod.main()

    learning = json.loads(learning_path.read_text(encoding="utf-8"))
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert learning["model_fingerprint"] == shadow["model_fingerprint"]
    assert learning["model_fingerprint"]["model_version"] == "us-compass-v1"
    assert learning["model_fingerprint"]["universe_count"] == 2
    assert learning["model_fingerprint"]["execution_basis"] == shadow["basis"]
    assert not (tmp_path / "public" / "data").exists()


def test_main_writes_rotation_research_and_history_with_valid_bar_cache(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    learning_path = tmp_path / "learning.json"
    shadow_path = tmp_path / "shadow.json"
    bar_path = tmp_path / "bars.db"
    pool_path.write_text(json.dumps({
        "model_date": "2026-03-64", "model_version": "v1", "market_regime": {"state": "震荡"},
        "rows": [
            {"symbol": "SPY", "theme": "大盘", "trend_score": 80, "trading_risk_score": 10,
             "trade_state": "可持有", "adjusted_close": 164, "day_open": 164, "price": 164},
            {"symbol": "QQQ", "theme": "科技", "trend_score": 90, "trading_risk_score": 10,
             "trade_state": "可持有", "adjusted_close": 196, "day_open": 196, "price": 196},
        ],
    }), encoding="utf-8")
    with sqlite3.connect(bar_path) as db:
        db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, close REAL, adj_close REAL, volume REAL, source TEXT, is_final INTEGER)")
        rows = []
        for symbol, slope in (("SPY", 1.0), ("QQQ", 1.5)):
            for index in range(1, 65):
                close = 100 + slope * index
                rows.append((symbol, f"2026-03-{index:02d}", close, close, 100, "yahoo", 1))
        db.executemany("INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", learning_path)
    monkeypatch.setattr(mod, "SHADOW", shadow_path)
    monkeypatch.setattr(mod, "BAR_DB", bar_path)
    mod.main()
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["rotation_research"]["model_date"] == "2026-03-64"
    assert shadow["rotation_research"]["production_weights_changed"] is False
    assert [row["symbol"] for row in shadow["rotation_research"]["selection"]] == ["QQQ", "SPY"]
    assert shadow["rotation_history"][0]["model_date"] == "2026-03-64"
    assert shadow["rotation_research"]["observation_gate"]["completed_days"] == 1


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
    monkeypatch.setattr(mod, "BAR_DB", tmp_path / "missing-bars.db")
    monkeypatch.setattr(mod, "atomic_write", lambda path, payload: written.append(payload))
    mod.main()
    learning_fp = written[0]["model_fingerprint"]
    shadow_fp = written[1]["model_fingerprint"]
    learning_fp["exposure_mapping"]["values"]["偏强"] = 0.25
    assert shadow_fp["exposure_mapping"]["values"]["偏强"] == 1.0


def test_main_keeps_breakout_result_when_rotation_builder_fails(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    learning_path = tmp_path / "learning.json"
    shadow_path = tmp_path / "shadow.json"
    bar_path = tmp_path / "bars.db"
    pool_path.write_text(json.dumps({
        "model_date": "2026-01-21", "model_version": "v1", "market_regime": {"state": "震荡"},
        "rows": [
            {"symbol": "SPY", "theme": "大盘", "trend_score": 80, "trading_risk_score": 10,
             "trade_state": "可持有", "adjusted_close": 102, "day_open": 102, "price": 102},
            {"symbol": "QQQ", "theme": "科技", "trend_score": 90, "trading_risk_score": 10,
             "trade_state": "可持有", "adjusted_close": 108, "day_open": 108, "price": 108},
        ],
    }), encoding="utf-8")
    with sqlite3.connect(bar_path) as db:
        db.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT, close REAL, adj_close REAL, volume REAL, source TEXT, is_final INTEGER)")
        rows = []
        for symbol, final_close in (("SPY", 102), ("QQQ", 108)):
            for day in range(1, 21):
                close = 100 + day % 2
                rows.append((symbol, f"2026-01-{day:02d}", close, close, 100, "yahoo", 1))
            rows.append((symbol, "2026-01-21", final_close, final_close, 260, "yahoo", 1))
        db.executemany("INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", learning_path)
    monkeypatch.setattr(mod, "SHADOW", shadow_path)
    monkeypatch.setattr(mod, "BAR_DB", bar_path)
    monkeypatch.setattr(mod, "build_rotation_shadow_report", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("rotation failed")))
    mod.main()
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["breakout_research"]["coverage"]["evaluated"] == 2
    assert len(shadow["breakout_history"]) == 1
    assert shadow["rotation_research"]["status"] == "UNAVAILABLE"
    assert shadow["rotation_research"]["reason"].endswith("ValueError")


def test_main_soft_fails_breakout_research_when_bar_database_is_invalid(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    learning_path = tmp_path / "learning.json"
    shadow_path = tmp_path / "shadow.json"
    old_history = [{"model_date": "2025-12-31", "hits": [{"symbol": "OLD"}]}]
    shadow_path.write_text(json.dumps({"breakout_history": old_history}), encoding="utf-8")
    bar_path = tmp_path / "bars.db"
    bar_path.write_text("not a sqlite database", encoding="utf-8")
    pool_path.write_text(json.dumps({
        "model_date": "2026-01-01", "model_version": "v1",
        "market_regime": {"state": "震荡"},
        "rows": [{"symbol": "SPY", "theme": "大盘", "trend_score": 80,
                  "trading_risk_score": 10, "trade_state": "可持有",
                  "adjusted_close": 100, "day_open": 100, "price": 100}],
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", learning_path)
    monkeypatch.setattr(mod, "SHADOW", shadow_path)
    monkeypatch.setattr(mod, "BAR_DB", bar_path)
    mod.main()
    assert learning_path.exists()
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["breakout_research"]["status"] == "UNAVAILABLE"
    assert shadow["breakout_research"]["reason"].endswith("DatabaseError")
    assert shadow["breakout_history"] == old_history


def test_main_preserves_breakout_and_rotation_history_when_bar_database_is_missing(tmp_path, monkeypatch):
    pool_path = tmp_path / "pool.json"
    learning_path = tmp_path / "learning.json"
    shadow_path = tmp_path / "shadow.json"
    old_history = [{"model_date": "2025-12-31", "hits": [{"symbol": "OLD"}]}]
    old_rotation = [{"model_date": "2025-12-31", "selection": [{"symbol": "QQQ"}]}]
    shadow_path.write_text(json.dumps({"breakout_history": old_history, "rotation_history": old_rotation}), encoding="utf-8")
    pool_path.write_text(json.dumps({
        "model_date": "2026-01-01", "model_version": "v1", "market_regime": {"state": "震荡"},
        "rows": [{"symbol": "SPY", "theme": "大盘", "trend_score": 80, "trading_risk_score": 10,
                  "trade_state": "可持有", "adjusted_close": 100, "day_open": 100, "price": 100}],
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "POOL", pool_path)
    monkeypatch.setattr(mod, "OUT", learning_path)
    monkeypatch.setattr(mod, "SHADOW", shadow_path)
    monkeypatch.setattr(mod, "BAR_DB", tmp_path / "missing.db")
    mod.main()
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["breakout_history"] == old_history
    assert shadow["rotation_history"] == old_rotation
    assert shadow["rotation_research"]["status"] == "UNAVAILABLE"
    assert shadow["rotation_research"]["production_change_allowed"] is False
