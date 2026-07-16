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
