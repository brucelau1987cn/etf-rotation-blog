#!/usr/bin/env python3
"""Generate an offline shadow-model snapshot from the local A-share ETF qfq cache.

This research layer never mutates production recommendations or model weights.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/local/etf-compass.db"
DEFAULT_OUT = ROOT / "public/data/model-lab/a-share-shadow.json"
DEFAULT_HISTORY = ROOT / "data/local/model-lab/a-share-shadow-history.jsonl"


def finite(value: Any, digits: int = 4) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    return (series - series.mean()) / std if std and math.isfinite(std) else series * 0


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
    rsi_delta = close.diff()
    gain = rsi_delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-rsi_delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
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
        "promotion_gate": {
            "minimum_trading_days": 20,
            "required_checks": ["rank_ic", "hit_rate", "max_drawdown", "turnover", "net_of_cost_increment"],
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
    history_path.write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in history), encoding="utf-8")
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
