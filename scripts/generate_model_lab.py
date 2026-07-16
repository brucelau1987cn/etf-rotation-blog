#!/usr/bin/env python3
"""Generate an offline shadow-model snapshot from the local A-share ETF qfq cache.

This research layer never mutates production recommendations or model weights.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/local/etf-compass.db"
DEFAULT_OUT = ROOT / "public/data/model-lab/a-share-shadow.json"
DEFAULT_HISTORY = ROOT / "data/local/model-lab/a-share-shadow-history.jsonl"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finite(value: Any, digits: int = 4) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    return (series - series.mean()) / std if std and math.isfinite(std) else series * 0


def rolling_zscore(series: pd.Series, window: int = 20) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0).replace(0, np.nan)
    return (series - mean) / std


def rsi_series(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100)
    return rsi.mask((loss == 0) & (gain == 0), 50)


def atr_series(df: pd.DataFrame, length: int = 14) -> pd.Series:
    previous = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous).abs(),
        (df["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def closed_weekly_frame(df: pd.DataFrame) -> pd.DataFrame:
    weekly = df.resample("W-FRI", label="right", closed="right").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "amount": "sum",
    }).dropna(subset=["close"])
    # A bucket labelled with a future Friday is still an open higher-timeframe bar.
    return weekly[weekly.index <= df.index[-1]]


def multi_timeframe_alignment(df: pd.DataFrame) -> dict[str, Any]:
    close = df["close"]
    daily_rsi = rsi_series(close)
    weekly = closed_weekly_frame(df)
    weekly_close = weekly["close"]
    weekly_rsi = rsi_series(weekly_close)
    votes: list[dict[str, Any]] = []

    def add(timeframe: str, method: str, value: Any) -> None:
        if value is None or pd.isna(value):
            return
        votes.append({"timeframe": timeframe, "method": method, "bullish": bool(value)})

    add("D", "EMA12>EMA26", close.ewm(span=12, adjust=False).mean().iloc[-1] > close.ewm(span=26, adjust=False).mean().iloc[-1])
    add("D", "Close>SMA20", close.iloc[-1] > close.rolling(20).mean().iloc[-1])
    add("D", "RSI14>50", daily_rsi.iloc[-1] > 50)
    if len(weekly_close) >= 10:
        add("W", "EMA4>EMA10", weekly_close.ewm(span=4, adjust=False).mean().iloc[-1] > weekly_close.ewm(span=10, adjust=False).mean().iloc[-1])
        add("W", "Close>SMA10", weekly_close.iloc[-1] > weekly_close.rolling(10).mean().iloc[-1])
        add("W", "RSI14>50", weekly_rsi.iloc[-1] > 50 if len(weekly_rsi.dropna()) else None)
    add("60D", "Close>SMA60", close.iloc[-1] > close.rolling(60).mean().iloc[-1])
    add("60D", "SMA20>SMA60", close.rolling(20).mean().iloc[-1] > close.rolling(60).mean().iloc[-1])
    add("60D", "Return60>0", close.pct_change(60).iloc[-1] > 0)
    bullish = sum(v["bullish"] for v in votes)
    available = len(votes)
    score = (2 * bullish / available - 1) * 100 if available else math.nan
    if score >= 55:
        state = "strong_bullish"
    elif score >= 20:
        state = "bullish"
    elif score <= -55:
        state = "strong_bearish"
    elif score <= -20:
        state = "bearish"
    else:
        state = "mixed"
    return {
        "score": finite(score, 2), "state": state, "bullish_votes": bullish,
        "available_votes": available, "closed_week_trade_date": weekly.index[-1].date().isoformat() if len(weekly) else None,
        "votes": votes,
    }


def atr_trailing_frame(df: pd.DataFrame, multiplier: float = 2.0) -> pd.DataFrame:
    close = df["close"]
    atr = atr_series(df)
    trend_up = close.ewm(span=12, adjust=False).mean() > close.ewm(span=26, adjust=False).mean()
    trails: list[float] = []
    breaches: list[bool] = []
    previous_trail = math.nan
    for i, (price, current_atr, uptrend) in enumerate(zip(close, atr, trend_up)):
        if not math.isfinite(float(current_atr)):
            trails.append(math.nan); breaches.append(False); continue
        raw = float(price - multiplier * current_atr)
        breached = i > 0 and math.isfinite(previous_trail) and float(price) < previous_trail
        if not bool(uptrend) or breached or not math.isfinite(previous_trail):
            current_trail = raw
        else:
            current_trail = max(previous_trail, raw)
        trails.append(current_trail); breaches.append(breached)
        previous_trail = current_trail
    return pd.DataFrame({"atr": atr, "trail": trails, "breach": breaches}, index=df.index)


def atr_trailing_defense(df: pd.DataFrame) -> dict[str, Any]:
    trail = atr_trailing_frame(df)
    price = float(df["close"].iloc[-1])
    atr = float(trail["atr"].iloc[-1])
    defense = float(trail["trail"].iloc[-1])
    distance_atr = (price - defense) / atr if math.isfinite(atr) and atr > 0 else math.nan
    breached = bool(trail["breach"].iloc[-1])
    state = "breached" if breached else "near" if math.isfinite(distance_atr) and distance_atr <= 1 else "above"
    return {
        "atr14": finite(atr), "trailing_defense": finite(defense),
        "distance_atr": finite(distance_atr, 2), "state": state, "multiplier": 2.0,
    }


def rsi_zscore_take_profit(df: pd.DataFrame) -> dict[str, Any]:
    rsi = rsi_series(df["close"])
    score = rolling_zscore(rsi, 20)
    current = float(score.iloc[-1]) if pd.notna(score.iloc[-1]) else math.nan
    previous = float(score.iloc[-2]) if len(score) > 1 and pd.notna(score.iloc[-2]) else math.nan
    if math.isfinite(previous) and previous >= 2 and current < 2:
        state = "cooling_trigger"
    elif current >= 2:
        state = "overheated"
    elif current <= -2:
        state = "oversold"
    else:
        state = "neutral"
    return {"rsi14": finite(rsi.iloc[-1], 2), "zscore20": finite(current, 2), "previous_zscore20": finite(previous, 2), "state": state, "threshold": 2.0}


def break_retest_audit(df: pd.DataFrame, lookback: int = 20, expiry_bars: int = 10) -> dict[str, Any]:
    zone = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    atr = atr_series(df)
    events: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    latest_state = "idle"
    for i in range(lookback, len(df)):
        price = float(df["close"].iloc[i]); opening = float(df["open"].iloc[i]); level = float(zone.iloc[i])
        current_atr = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else math.nan
        if active is None:
            previous_close = float(df["close"].iloc[i - 1])
            if math.isfinite(level) and math.isfinite(current_atr) and opening <= level < price and previous_close <= level:
                active = {"breakout_index": i, "breakout_date": df.index[i].date().isoformat(), "level": level, "atr": current_atr}
                latest_state = "armed"
            continue
        elapsed = i - int(active["breakout_index"])
        active_level = float(active["level"]); active_atr = float(active["atr"])
        if price < active_level - active_atr:
            events.append({**active, "status": "failed", "event_date": df.index[i].date().isoformat(), "bars": elapsed})
            active = None; latest_state = "failed"; continue
        if elapsed >= 1 and float(df["low"].iloc[i]) <= active_level + .25 * active_atr and price >= active_level:
            entry = price; stop = active_level - active_atr; risk = max(entry - stop, .01 * entry); target = entry + 2 * risk
            outcome = "open"; outcome_date = None
            horizon = df.iloc[i + 1:i + 1 + expiry_bars]
            for event_date, row in horizon.iterrows():
                stop_hit = float(row["low"]) <= stop; target_hit = float(row["high"]) >= target
                if stop_hit:
                    outcome = "loss"; outcome_date = event_date.date().isoformat(); break
                if target_hit:
                    outcome = "win"; outcome_date = event_date.date().isoformat(); break
            if outcome == "open" and len(horizon) >= expiry_bars:
                outcome = "expired"; outcome_date = horizon.index[-1].date().isoformat()
            events.append({**active, "status": "confirmed", "event_date": df.index[i].date().isoformat(), "bars": elapsed,
                           "entry": finite(entry), "stop": finite(stop), "target": finite(target), "outcome": outcome, "outcome_date": outcome_date})
            active = None; latest_state = "confirmed"; continue
        if elapsed > expiry_bars:
            events.append({**active, "status": "expired", "event_date": df.index[i].date().isoformat(), "bars": elapsed})
            active = None; latest_state = "expired"
    if active is not None:
        latest_state = "armed"
    elif events and events[-1].get("event_date") != df.index[-1].date().isoformat():
        latest_state = "idle"
    confirmed = [x for x in events if x["status"] == "confirmed"]
    wins = sum(x.get("outcome") == "win" for x in confirmed)
    losses = sum(x.get("outcome") == "loss" for x in confirmed)
    outcome_expired = sum(x.get("outcome") == "expired" for x in confirmed)
    outcome_open = sum(x.get("outcome") == "open" for x in confirmed)
    decided = wins + losses
    return {
        "state": latest_state, "active_level": finite(active["level"]) if active else None,
        "event_count": len(events), "confirmed_count": len(confirmed), "wins": wins, "losses": losses,
        "outcome_expired": outcome_expired, "outcome_open": outcome_open, "decided_count": decided,
        "win_rate": finite(wins / decided * 100, 1) if decided else None,
        "recent_events": events[-5:], "rules": {"lookback": lookback, "expiry_bars": expiry_bars, "retest_tolerance_atr": .25, "target_r": 2.0},
    }


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns.fillna(0)).cumprod()
    drawdown = equity / equity.cummax() - 1
    return float(drawdown.min()) if len(drawdown) else math.nan


def load_frames(db_path: Path, limit: int) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    names = {r["symbol"]: r["name"] for r in db.execute(
        "SELECT symbol,name FROM instruments WHERE market IN ('A','XSHG','XSHE') AND active=1"
    )}
    rows = db.execute(
        """SELECT symbol,trade_date,open,high,low,close,volume,amount FROM (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY symbol,trade_date
            ORDER BY CASE source WHEN 'iwencai' THEN 1 WHEN 'stock-api' THEN 2 ELSE 9 END,
                     fetched_at DESC
          ) rn
          FROM daily_bars
          WHERE market IN ('A','XSHG','XSHE') AND adjustment='qfq' AND is_final=1
        ) WHERE rn=1 ORDER BY symbol,trade_date"""
    ).fetchall()
    db.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["symbol"], []).append(dict(row))
    frames: dict[str, pd.DataFrame] = {}
    for symbol, data in grouped.items():
        df = pd.DataFrame(data).tail(limit).copy()
        if len(df) < 60:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        for col in ("open", "high", "low", "close", "volume", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        frames[symbol] = df
    return frames, names


def symbol_metrics(df: pd.DataFrame) -> dict[str, Any]:
    close = df["close"]
    returns = close.pct_change()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    rsi = rsi_series(close)
    hv20 = returns.rolling(20).std() * np.sqrt(252)
    hv_pct = hv20.rolling(120, min_periods=60).rank(pct=True) * 100
    vol_ma20 = df["volume"].rolling(20).mean()
    amount_ma20 = df["amount"].rolling(20).mean()
    ret20 = close.pct_change(20)
    ret60 = close.pct_change(60)
    trend = 1 if ema12.iloc[-1] > ema26.iloc[-1] else -1
    volume_confirm = 1 if df["volume"].iloc[-1] > vol_ma20.iloc[-1] else 0
    technical_vote = trend + volume_confirm
    if rsi.iloc[-1] < 30:
        technical_vote += 1
    elif rsi.iloc[-1] > 70:
        technical_vote -= 1
    return {
        "trade_date": df.index[-1].date().isoformat(),
        "close": finite(close.iloc[-1]),
        "ret20": finite(ret20.iloc[-1] * 100),
        "ret60": finite(ret60.iloc[-1] * 100),
        "rsi14": finite(rsi.iloc[-1], 2),
        "ema12": finite(ema12.iloc[-1]),
        "ema26": finite(ema26.iloc[-1]),
        "technical_vote": int(technical_vote),
        "hv20_annualized": finite(hv20.iloc[-1] * 100),
        "hv_percentile": finite(hv_pct.iloc[-1], 2),
        "max_drawdown60": finite(max_drawdown(returns.tail(60)) * 100),
        "amount_ma20": finite(amount_ma20.iloc[-1], 2),
        "signal_enhancement": {
            "multi_timeframe": multi_timeframe_alignment(df),
            "atr_defense": atr_trailing_defense(df),
            "rsi_take_profit": rsi_zscore_take_profit(df),
            "break_retest": break_retest_audit(df),
        },
    }


def sample_stats(values: list[float]) -> dict[str, Any]:
    clean = [float(x) for x in values if math.isfinite(float(x))]
    if not clean:
        return {"count": 0, "average_pct": None, "median_pct": None, "positive_rate": None}
    return {
        "count": len(clean), "average_pct": finite(np.mean(clean) * 100, 3),
        "median_pct": finite(np.median(clean) * 100, 3),
        "positive_rate": finite(sum(x > 0 for x in clean) / len(clean) * 100, 1),
    }


def alignment_score_series(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    weekly = closed_weekly_frame(df)
    weekly_close = weekly["close"]
    weekly_votes = pd.DataFrame(index=weekly.index)
    weekly_fast = weekly_close.ewm(span=4, adjust=False).mean(); weekly_slow = weekly_close.ewm(span=10, adjust=False).mean()
    weekly_sma = weekly_close.rolling(10).mean(); weekly_rsi = rsi_series(weekly_close)
    weekly_votes["ema"] = (weekly_fast > weekly_slow).astype(float)
    weekly_votes["sma"] = (weekly_close > weekly_sma).astype(float).where(weekly_sma.notna())
    weekly_votes["rsi"] = (weekly_rsi > 50).astype(float).where(weekly_rsi.notna())
    weekly_daily = weekly_votes.astype(float).reindex(df.index, method="ffill")
    votes = pd.DataFrame(index=df.index)
    votes["d_ema"] = (close.ewm(span=12, adjust=False).mean() > close.ewm(span=26, adjust=False).mean()).astype(float)
    daily_sma = close.rolling(20).mean(); daily_rsi = rsi_series(close)
    votes["d_sma"] = (close > daily_sma).astype(float).where(daily_sma.notna())
    votes["d_rsi"] = (daily_rsi > 50).astype(float).where(daily_rsi.notna())
    for column in weekly_daily:
        votes[f"w_{column}"] = weekly_daily[column]
    sma20 = close.rolling(20).mean(); sma60 = close.rolling(60).mean(); ret60 = close.pct_change(60)
    votes["l_close"] = (close > sma60).astype(float).where(sma60.notna())
    votes["l_sma"] = (sma20 > sma60).astype(float).where(sma60.notna())
    votes["l_ret"] = (ret60 > 0).astype(float).where(ret60.notna())
    available = votes.notna().sum(axis=1).replace(0, np.nan)
    return (2 * votes.sum(axis=1) / available - 1) * 100


def historical_feature_validation(frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    aligned: list[float] = []; mixed: list[float] = []; breaches: list[float] = []; cooling: list[float] = []
    for df in frames.values():
        close = df["close"]
        forward5 = close.shift(-5) / close - 1
        alignment = alignment_score_series(df)
        for value, future in zip(alignment, forward5):
            if not math.isfinite(float(future)) or not math.isfinite(float(value)):
                continue
            (aligned if value >= 55 else mixed).append(float(future))
        trail = atr_trailing_frame(df)
        breaches.extend(float(future) for flag, future in zip(trail["breach"], forward5) if bool(flag) and math.isfinite(float(future)))
        rsi_z = rolling_zscore(rsi_series(close), 20)
        cool = (rsi_z.shift(1) >= 2) & (rsi_z < 2)
        cooling.extend(float(future) for flag, future in zip(cool, forward5) if bool(flag) and math.isfinite(float(future)))
    aligned_stats = sample_stats(aligned); mixed_stats = sample_stats(mixed)
    return {
        "basis": "closed qfq daily bars; forward 5-session close return; research-only",
        "multi_timeframe": {
            "strong_alignment": aligned_stats, "other_states": mixed_stats,
            "average_return_lift_pct": finite((aligned_stats.get("average_pct") or 0) - (mixed_stats.get("average_pct") or 0), 3),
        },
        "atr_breach": {**sample_stats(breaches), "interpretation": "forward return after close breaches prior ratcheted ATR defense"},
        "rsi_cooling": {**sample_stats(cooling), "interpretation": "forward return after RSI Z-score cools below +2"},
    }


def enhancement_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    enhancements = [x["signal_enhancement"] for x in items]
    alignment_states = {state: sum(x["multi_timeframe"]["state"] == state for x in enhancements) for state in
                        ("strong_bullish", "bullish", "mixed", "bearish", "strong_bearish")}
    atr_states = {state: sum(x["atr_defense"]["state"] == state for x in enhancements) for state in ("above", "near", "breached")}
    rsi_states = {state: sum(x["rsi_take_profit"]["state"] == state for x in enhancements) for state in
                  ("overheated", "cooling_trigger", "neutral", "oversold")}
    break_states = {state: sum(x["break_retest"]["state"] == state for x in enhancements) for state in
                    ("armed", "confirmed", "failed", "expired", "idle")}
    confirmed = sum(x["break_retest"]["confirmed_count"] for x in enhancements)
    wins = sum(x["break_retest"]["wins"] for x in enhancements)
    losses = sum(x["break_retest"]["losses"] for x in enhancements)
    outcome_expired = sum(x["break_retest"]["outcome_expired"] for x in enhancements)
    outcome_open = sum(x["break_retest"]["outcome_open"] for x in enhancements)
    decided = wins + losses
    return {
        "multi_timeframe": alignment_states, "atr_defense": atr_states, "rsi_take_profit": rsi_states,
        "break_retest": {**break_states, "historical_confirmed": confirmed, "decided_count": decided,
                         "wins": wins, "losses": losses, "outcome_expired": outcome_expired, "outcome_open": outcome_open,
                         "win_rate": finite(wins / decided * 100, 1) if decided else None},
    }


def portfolio_risk(frames: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, Any]:
    returns = pd.concat({s: frames[s]["close"].pct_change() for s in symbols}, axis=1).dropna().tail(120)
    if returns.empty:
        return {}
    portfolio = returns.mean(axis=1)
    var95 = float(portfolio.quantile(.05))
    tail = portfolio[portfolio <= var95]
    return {
        "symbols": symbols,
        "observations": len(portfolio),
        "annualized_volatility_pct": finite(portfolio.std() * np.sqrt(252) * 100),
        "var95_daily_pct": finite(var95 * 100),
        "cvar95_daily_pct": finite(tail.mean() * 100),
        "max_drawdown_pct": finite(max_drawdown(portfolio) * 100),
    }


def correlation_summary(frames: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, Any]:
    returns = pd.concat({s: frames[s]["close"].pct_change() for s in symbols}, axis=1).dropna().tail(120)
    if returns.empty:
        return {}
    corr = returns.corr()
    pairs = []
    for i, left in enumerate(corr.columns):
        for right in corr.columns[i + 1:]:
            pairs.append({"left": left, "right": right, "correlation": finite(corr.loc[left, right], 3)})
    pairs.sort(key=lambda x: abs(x["correlation"] or 0), reverse=True)
    mask = np.triu(np.ones(corr.shape, dtype=bool), 1)
    values = corr.where(mask).stack()
    return {
        "observations": len(returns),
        "mean_pairwise_correlation": finite(values.mean(), 3),
        "highest_pairs": pairs[:10],
    }


def execution_estimate(metric: dict[str, Any], order_cny: float) -> dict[str, Any]:
    avg_amount = metric.get("amount_ma20") or 0
    participation = order_cny / avg_amount if avg_amount > 0 else math.nan
    # Conservative research-only square-root impact proxy; calibrated later from paper fills.
    impact_bps = 2 + 10 * math.sqrt(max(participation, 0)) if math.isfinite(participation) else math.nan
    return {
        "order_cny": order_cny,
        "participation_pct": finite(participation * 100, 3),
        "estimated_impact_bps": finite(impact_bps, 2),
        "formula": "2 + 10*sqrt(order/avg_amount_20d)",
    }


def generate(db_path: Path, out_path: Path, history_path: Path, limit: int = 180) -> dict[str, Any]:
    frames, names = load_frames(db_path, limit)
    metrics = {symbol: symbol_metrics(df) for symbol, df in frames.items()}
    formal_count = len(metrics)
    defense_monitor: list[dict[str, Any]] = []
    universe_path = ROOT / "data/etf-universe.json"
    if universe_path.exists():
        universe = json.loads(universe_path.read_text(encoding="utf-8"))
        rotation_codes = {x["code"] for x in universe["items"] if x["tier"] == "formal" and x["asset_layer"] == "rotation"}
        defense_codes = {x["code"] for x in universe["items"] if x["tier"] == "formal" and x["asset_layer"] == "defense"}
        if db_path.resolve() == DEFAULT_DB.resolve():
            formal_count = universe["counts"]["formal"]
            defense_monitor = [
                {"symbol": symbol, "name": names.get(symbol, symbol), **metrics[symbol]}
                for symbol in sorted(set(metrics) & defense_codes)
            ]
            frames = {symbol: frame for symbol, frame in frames.items() if symbol in rotation_codes}
            metrics = {symbol: metric for symbol, metric in metrics.items() if symbol in rotation_codes}
    table = pd.DataFrame(metrics).T
    for col in ("ret20", "ret60", "technical_vote", "hv20_annualized", "max_drawdown60"):
        table[col] = pd.to_numeric(table[col], errors="coerce")
    table["factor_score"] = (
        .35 * zscore(table["ret20"]) + .25 * zscore(table["ret60"])
        + .20 * zscore(table["technical_vote"]) - .10 * zscore(table["hv20_annualized"])
        + .10 * zscore(table["max_drawdown60"])
    )
    ranked = list(table.sort_values("factor_score", ascending=False).index)
    top = ranked[:12]
    items = []
    for symbol in ranked:
        metric = metrics[symbol]
        items.append({
            "symbol": symbol,
            "name": names.get(symbol, symbol),
            **metric,
            "factor_score": finite(table.loc[symbol, "factor_score"]),
            "shadow_rank": ranked.index(symbol) + 1,
            "execution": execution_estimate(metric, 100_000),
        })
    snapshot = {
        "schema_version": 1,
        "mode": "shadow_research_only",
        "production_weights_changed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_trade_date": max((x["trade_date"] for x in metrics.values()), default=None),
        "universe_count": len(items),
        "formal_universe_count": formal_count,
        "rotation_universe_count": len(items),
        "defense_monitor": defense_monitor,
        "minimum_observations": 60,
        "factor_definition": {
            "ret20": .35, "ret60": .25, "technical_vote": .20,
            "volatility_penalty": -.10, "drawdown_quality": .10,
        },
        "shadow_top12": top,
        "portfolio_risk": portfolio_risk(frames, top),
        "correlation": correlation_summary(frames, top),
        "items": items,
        "signal_enhancement": {
            "version": "A ETF Sidecar Signals v1",
            "production_role": "shadow_filter_and_audit_only",
            "formal_signal_logic_changed": False,
            "methodology_provenance": "independent Python implementation from public methodology descriptions; no Pine source embedded",
            "summary": enhancement_summary(items),
            "historical_validation": historical_feature_validation(frames),
            "features": [
                "closed multi-timeframe alignment",
                "2x ATR ratcheted defense",
                "RSI14 rolling Z-score take-profit",
                "20-session breakout-retest state machine",
            ],
        },
        "promotion_gate": {
            "minimum_trading_days": 20,
            "required_checks": ["rank_ic", "hit_rate", "max_drawdown", "turnover", "net_of_cost_increment"],
        },
    }
    # Prepare both destinations before publishing either artifact. The public
    # snapshot is replaced last, after the matching history row is durable.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("latest_trade_date") != snapshot["latest_trade_date"]:
                    history.append(item)
            except json.JSONDecodeError:
                continue
    history.append(snapshot)
    history = sorted(history, key=lambda x: x.get("latest_trade_date") or "")[-500:]
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    history_text = "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in history)
    atomic_write_text(history_path, history_text)
    atomic_write_text(out_path, snapshot_text)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--limit", type=int, default=180)
    args = parser.parse_args()
    snapshot = generate(args.db, args.out, args.history, args.limit)
    print(json.dumps({
        "status": "ok", "mode": snapshot["mode"], "universe": snapshot["universe_count"],
        "trade_date": snapshot["latest_trade_date"], "top12": snapshot["shadow_top12"],
    }, ensure_ascii=False))
    return 0 if snapshot["universe_count"] >= 82 else 2


if __name__ == "__main__":
    raise SystemExit(main())
