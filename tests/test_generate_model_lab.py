from __future__ import annotations

import importlib.util
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("generate_model_lab", ROOT / "scripts/generate_model_lab.py")
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def seed_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE instruments(market TEXT,symbol TEXT,name TEXT,asset_type TEXT,active INTEGER,updated_at TEXT,PRIMARY KEY(market,symbol));
    CREATE TABLE daily_bars(market TEXT,symbol TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,adjustment TEXT,source TEXT,is_final INTEGER,fetched_at TEXT,PRIMARY KEY(market,symbol,trade_date,adjustment,source));
    """)
    start = date(2025, 1, 1)
    rng = np.random.default_rng(7)
    for n, symbol in enumerate(("510300", "510500", "159915", "588000", "518880")):
        db.execute("INSERT INTO instruments VALUES(?,?,?,?,?,?)", ("A", symbol, f"ETF-{symbol}", "ETF", 1, "2026-01-01"))
        price = 100 + n
        for i in range(180):
            day = start + timedelta(days=i)
            price *= math.exp(float(rng.normal(.0004 + n * .00005, .01)))
            db.execute("INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "A", symbol, day.isoformat(), price * .995, price * 1.01, price * .99, price,
                1_000_000 + i * 1000, 200_000_000 + i * 10000, "qfq", "iwencai", 1, "2026-01-01",
            ))
    db.commit(); db.close()


def test_generate_shadow_snapshot(tmp_path: Path):
    db = tmp_path / "lab.db"; out = tmp_path / "shadow.json"; history = tmp_path / "history.jsonl"
    seed_db(db)
    snapshot = lab.generate(db, out, history)
    assert snapshot["mode"] == "shadow_research_only"
    assert snapshot["production_weights_changed"] is False
    assert snapshot["universe_count"] == 5
    assert len(snapshot["shadow_top12"]) == 5
    assert snapshot["portfolio_risk"]["observations"] > 0
    assert snapshot["correlation"]["highest_pairs"]
    assert out.exists() and history.exists()
    assert all(item["execution"]["estimated_impact_bps"] for item in snapshot["items"])


def test_history_destination_failure_does_not_publish_snapshot(tmp_path: Path):
    db = tmp_path / "lab.db"; out = tmp_path / "shadow.json"
    seed_db(db)
    history = Path("/proc/etf-model-lab/history.jsonl")
    try:
        lab.generate(db, out, history)
    except OSError:
        pass
    else:
        raise AssertionError("expected history destination failure")
    assert not out.exists()


def market_frame(closes: list[float]) -> pd.DataFrame:
    close = pd.Series(closes, index=pd.bdate_range("2025-01-02", periods=len(closes)), dtype=float)
    return pd.DataFrame({
        "open": close * .998, "high": close * 1.005, "low": close * .995,
        "close": close, "volume": 1_000_000.0, "amount": close * 1_000_000,
    }, index=close.index)


def test_multi_timeframe_alignment_uses_closed_week_and_detects_trend():
    frame = market_frame([100 + i * .5 for i in range(100)])
    result = lab.multi_timeframe_alignment(frame)
    assert result["score"] >= 55
    assert result["state"] == "strong_bullish"
    assert result["closed_week_trade_date"] <= frame.index[-1].date().isoformat()
    assert lab.rsi_series(frame["close"]).iloc[-1] == 100


def test_atr_trail_is_below_price_in_stable_uptrend():
    frame = market_frame([100 + i * .3 for i in range(100)])
    result = lab.atr_trailing_defense(frame)
    assert result["trailing_defense"] < frame["close"].iloc[-1]
    assert result["state"] in {"above", "near"}


def test_break_retest_state_machine_confirms_and_audits_outcome():
    closes = [100 + i * .1 for i in range(25)] + [105, 103.1, 108, 109, 110]
    frame = market_frame(closes)
    frame.iloc[25, frame.columns.get_loc("open")] = 102.0
    frame.iloc[26, frame.columns.get_loc("low")] = 102.2
    frame.iloc[27, frame.columns.get_loc("high")] = 112.0
    result = lab.break_retest_audit(frame)
    assert result["confirmed_count"] >= 1
    assert result["wins"] >= 1
    confirmed = [x for x in result["recent_events"] if x["status"] == "confirmed"]
    assert confirmed and confirmed[-1]["bars"] >= 1


def test_sequoia_pattern_metrics_are_etf_adapted_and_research_only():
    closes = [100 + index * .35 for index in range(130)]
    frame = market_frame(closes)
    frame.iloc[-1, frame.columns.get_loc("open")] = frame["close"].iloc[-1] * .99
    frame.iloc[-1, frame.columns.get_loc("close")] = frame["high"].iloc[-2] * 1.01
    frame.iloc[-1, frame.columns.get_loc("high")] = frame["close"].iloc[-1] * 1.003
    frame.iloc[-1, frame.columns.get_loc("low")] = frame["close"].iloc[-1] * .995
    frame.iloc[-1, frame.columns.get_loc("amount")] = frame["amount"].iloc[-2] * 2
    result = lab.sequoia_pattern_metric(frame)
    assert result["turtle_confirmed"]["triggered"] is True
    assert result["turtle_confirmed"]["prior_high_20"] < frame["close"].iloc[-1]
    assert result["production_change_allowed"] is False
    assert result["signal_version"] == "a-etf-pattern-shadow-v1"


def test_high_tight_flag_uses_atr_adaptive_consolidation():
    closes = [100.0] * 90 + [100 + index * 1.2 for index in range(30)] + [134.0] * 10
    frame = market_frame(closes)
    frame.loc[frame.index[-10]:, "high"] = 134.5
    frame.loc[frame.index[-10]:, "low"] = 133.5
    frame.loc[frame.index[-1], "amount"] = frame["amount"].iloc[-2] * .5
    result = lab.sequoia_pattern_metric(frame)["high_tight_flag"]
    assert result["triggered"] is True
    assert result["consolidation_limit_pct"] >= 8


def test_rps_percentiles_are_cross_sectional_and_fail_closed_for_short_history():
    frames = {
        "FAST": market_frame([100 + index for index in range(130)]),
        "SLOW": market_frame([100 + index * .1 for index in range(130)]),
        "SHORT": market_frame([100 + index for index in range(60)]),
    }
    result = lab.rps_breakout_metrics(frames)
    assert result["FAST"]["rps120"] == 100.0
    assert result["FAST"]["triggered"] is True
    assert result["SHORT"]["status"] == "UNAVAILABLE"


def test_generated_snapshot_contains_isolated_pattern_research(tmp_path: Path):
    db = tmp_path / "lab.db"; out = tmp_path / "shadow.json"; history = tmp_path / "history.jsonl"
    seed_db(db)
    snapshot = lab.generate(db, out, history)
    research = snapshot["pattern_research"]
    assert research["mode"] == "shadow_research_only"
    assert research["production_change_allowed"] is False
    assert research["production_weights_changed"] is False
    assert research["observation_gate"]["minimum_completed_days"] == 10
    assert research["observation_gate"]["preferred_completed_days"] == 20
    assert set(research["signals"]) == {"rps_breakout", "high_tight_flag", "turtle_confirmed"}
    assert research["coverage"]["requested"] == 5
    assert all("pattern_research" not in item for item in snapshot["items"])


def test_pattern_observation_days_are_idempotent(tmp_path: Path):
    db = tmp_path / "lab.db"; out = tmp_path / "shadow.json"; history = tmp_path / "history.jsonl"
    seed_db(db)
    first = lab.generate(db, out, history)
    second = lab.generate(db, out, history)
    assert first["pattern_research"]["observation_gate"]["completed_days"] == 1
    assert second["pattern_research"]["observation_gate"]["completed_days"] == 1


def test_pattern_metrics_reject_nonfinite_recent_values():
    frame = market_frame([100 + index * .2 for index in range(130)])
    frame.iloc[-1, frame.columns.get_loc("amount")] = math.inf
    result = lab.sequoia_pattern_metric(frame)
    assert result["status"] == "ok"
    assert result["turtle_confirmed"]["status"] == "UNAVAILABLE"
    frame = market_frame([100 + index * .2 for index in range(130)])
    frame.iloc[-2, frame.columns.get_loc("high")] = math.inf
    assert lab.rps_breakout_metrics({"BAD": frame})["BAD"]["status"] == "UNAVAILABLE"


def test_pattern_gate_has_three_explicit_observation_phases():
    assert lab.pattern_observation_gate(9)["status"] == "ACCUMULATING"
    assert lab.pattern_observation_gate(9)["eligible_for_evaluation"] is False
    assert lab.pattern_observation_gate(10)["status"] == "EVALUATING"
    assert lab.pattern_observation_gate(10)["eligible_for_evaluation"] is True
    assert lab.pattern_observation_gate(20)["status"] == "OBSERVING"


def test_participation_signals_use_amount_across_mixed_sources():
    frame = market_frame([100 + index * .2 for index in range(130)])
    frame["source"] = "tencent"
    frame.iloc[-2, frame.columns.get_loc("source")] = "iwencai"
    result = lab.sequoia_pattern_metric(frame)
    assert result["status"] == "ok"
    assert result["turtle_confirmed"]["amount_ratio_20"] is not None


def test_rps_rejects_stale_cross_section_member():
    fresh = market_frame([100 + index for index in range(130)])
    stale = market_frame([100 + index * .1 for index in range(130)])
    stale.index = stale.index - timedelta(days=1)
    result = lab.rps_breakout_metrics({"FRESH": fresh, "STALE": stale})
    assert result["FRESH"]["status"] == "ok"
    assert result["STALE"]["status"] == "UNAVAILABLE"
    assert "common trade date" in result["STALE"]["reason"]


def test_generator_source_uses_strict_json_serialization():
    source = (ROOT / "scripts/generate_garden_pool.py").read_text(encoding="utf-8")
    assert "sanitize_for_json" in source
    assert "allow_nan=False" in source
