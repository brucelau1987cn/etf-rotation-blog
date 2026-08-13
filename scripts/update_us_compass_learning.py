#!/usr/bin/env python3
"""Forward-only self-evaluation for the US ETF Compass.

Freezes each daily 74-ETF ranking, matures T+1/T+5/T+20 outcomes, computes
cross-sectional RankIC/deviation, and maintains four open-to-open shadow
portfolios. No brokerage/account access and no production-weight mutation.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import math
import sqlite3
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .us_compass_fingerprint import build_model_fingerprint
    from .us_compass_research_metrics import ranks, spearman
else:
    fingerprint_path = Path(__file__).resolve().with_name("us_compass_fingerprint.py")
    fingerprint_name = f"_us_compass_fingerprint_{id(fingerprint_path)}"
    fingerprint_spec = importlib.util.spec_from_file_location(fingerprint_name, fingerprint_path)
    if fingerprint_spec is None or fingerprint_spec.loader is None:
        raise ImportError(f"cannot load model fingerprint from {fingerprint_path}")
    fingerprint_module = importlib.util.module_from_spec(fingerprint_spec)
    fingerprint_spec.loader.exec_module(fingerprint_module)
    build_model_fingerprint = fingerprint_module.build_model_fingerprint

    metrics_path = Path(__file__).resolve().with_name("us_compass_research_metrics.py")
    metrics_name = f"_us_compass_research_metrics_{id(metrics_path)}"
    metrics_spec = importlib.util.spec_from_file_location(metrics_name, metrics_path)
    if metrics_spec is None or metrics_spec.loader is None:
        raise ImportError(f"cannot load research metrics from {metrics_path}")
    metrics_module = importlib.util.module_from_spec(metrics_spec)
    metrics_spec.loader.exec_module(metrics_module)
    ranks = metrics_module.ranks
    spearman = metrics_module.spearman

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "public/data/us-etf-pool.json"
OUT = ROOT / "public/data/us-compass-learning.json"
SHADOW = ROOT / "public/data/us-compass-shadow.json"
BAR_DB = ROOT / "data/local/us-etf-compass.db"
HORIZONS = (1, 5, 20)
INITIAL_CAPITAL = 20_000.0
ONE_WAY_COST = 0.001
EXECUTION_BASIS = "T close signal; T+1 open execution; next-open rebalance"
EXPOSURE_MAPPING = {"偏强": 1.0, "震荡": 0.5, "防御": 0.0}
DEFAULT_EXPOSURE = 0.5
BREAKOUT_MIN_RETURN = 0.03
BREAKOUT_MIN_RELATIVE_VOLUME = 2.0
BREAKOUT_MIN_VOLATILITY_MOVE = 2.0
BREAKOUT_MIN_RELATIVE_SPY = 0.01
BREAKOUT_UNAVAILABLE_REASON = "requires at least 21 final daily bars with positive prices and 10-day volume history"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def percentile_ranks(values: list[float]) -> list[float]:
    rr = ranks(values)
    n = len(values)
    return [((r - 1) / max(n - 1, 1)) for r in rr]


def exposure_for(regime: str) -> float:
    return EXPOSURE_MAPPING.get(regime, DEFAULT_EXPOSURE)


def breakout_shadow_metric(
    symbol: str, bars: list[dict[str, Any]], *, spy_return: float, expected_trade_date: str | None = None,
) -> dict[str, Any]:
    """Calculate a research-only ETF breakout label from completed daily bars."""
    if not math.isfinite(spy_return):
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": "SPY benchmark return must be finite"}
    ordered = sorted(bars, key=lambda row: str(row.get("trade_date") or ""))
    if len(ordered) < 21:
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": BREAKOUT_UNAVAILABLE_REASON}
    if expected_trade_date and ordered[-1].get("trade_date") != expected_trade_date:
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": "latest final bar does not match model_date"}
    closes: list[float] = []
    volumes: list[float] = []
    try:
        for row in ordered[-21:]:
            raw_close = row.get("adj_close") if row.get("adj_close") is not None else row.get("close")
            raw_volume = row.get("volume")
            if raw_close is None or raw_volume is None:
                raise ValueError
            close = float(raw_close)
            volume = float(raw_volume)
            if not math.isfinite(close) or close <= 0 or not math.isfinite(volume) or volume < 0:
                raise ValueError
            closes.append(close)
            volumes.append(volume)
    except (TypeError, ValueError):
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": BREAKOUT_UNAVAILABLE_REASON}
    average_volume = statistics.fmean(volumes[-11:-1])
    if average_volume <= 0:
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": BREAKOUT_UNAVAILABLE_REASON}
    historical_returns = [closes[index] / closes[index - 1] - 1 for index in range(1, 20)]
    try:
        daily_return = closes[-1] / closes[-2] - 1
    except OverflowError:
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": "derived breakout metrics must be finite"}
    volatility = statistics.stdev(historical_returns) if len(historical_returns) >= 2 else 0.0
    adjusted_move = daily_return / volatility if volatility > 0 else None
    relative_volume = volumes[-1] / average_volume
    relative_spy = daily_return - spy_return
    if not all(math.isfinite(value) for value in (daily_return, volatility, relative_volume, relative_spy)):
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": "derived breakout metrics must be finite"}
    if adjusted_move is not None and not math.isfinite(adjusted_move):
        return {"symbol": symbol, "status": "UNAVAILABLE", "reason": "derived breakout metrics must be finite"}
    triggered = (
        daily_return >= BREAKOUT_MIN_RETURN
        and relative_volume >= BREAKOUT_MIN_RELATIVE_VOLUME
        and adjusted_move is not None
        and adjusted_move >= BREAKOUT_MIN_VOLATILITY_MOVE
        and relative_spy >= BREAKOUT_MIN_RELATIVE_SPY
    )
    return {
        "symbol": symbol,
        "status": "BREAKOUT" if triggered else "NORMAL",
        "trade_date": ordered[-1].get("trade_date"),
        "daily_return": round(daily_return, 6),
        "relative_volume_10d": round(relative_volume, 6),
        "volatility20": round(volatility, 6),
        "volatility_adjusted_move": round(adjusted_move, 6) if adjusted_move is not None else None,
        "relative_spy": round(relative_spy, 6),
    }


def build_breakout_shadow_report(
    db: sqlite3.Connection, *, model_date: str, pool_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    db.row_factory = sqlite3.Row
    symbols = [str(row.get("symbol") or "") for row in pool_rows if row.get("symbol")]
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        rows = db.execute(
            """SELECT trade_date, close, adj_close, volume FROM daily_bars
               WHERE symbol=? AND source='yahoo' AND is_final=1 AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 21""",
            (symbol, model_date),
        ).fetchall()
        bars_by_symbol[symbol] = [dict(row) for row in reversed(rows)]
    spy_bars = bars_by_symbol.get("SPY", [])
    spy_metric = breakout_shadow_metric("SPY", spy_bars, spy_return=0.0, expected_trade_date=model_date)
    if spy_metric.get("status") == "UNAVAILABLE":
        return {
            "version": 1, "mode": "shadow_research_only", "production_change_allowed": False,
            "model_date": model_date, "status": "UNAVAILABLE",
            "reason": "SPY benchmark return is unavailable for model_date",
            "coverage": {"requested": len(symbols), "evaluated": 0, "unavailable": len(symbols)},
            "hits": [], "metrics": [],
        }
    spy_return = float(spy_metric["daily_return"])
    metrics = [
        breakout_shadow_metric(symbol, bars_by_symbol[symbol], spy_return=spy_return, expected_trade_date=model_date)
        for symbol in symbols
    ]
    themes = {str(row.get("symbol")): row.get("theme") for row in pool_rows}
    for metric in metrics:
        metric["theme"] = themes.get(metric["symbol"])
    hits = sorted(
        (metric for metric in metrics if metric["status"] == "BREAKOUT"),
        key=lambda metric: (metric.get("volatility_adjusted_move") or 0, metric.get("relative_volume_10d") or 0),
        reverse=True,
    )
    unavailable = sum(metric["status"] == "UNAVAILABLE" for metric in metrics)
    return {
        "version": 1,
        "mode": "shadow_research_only",
        "production_change_allowed": False,
        "model_date": model_date,
        "basis": "completed Yahoo daily bars; current return / prior 19-return sample volatility; current volume / prior 10-day mean",
        "thresholds": {
            "daily_return": BREAKOUT_MIN_RETURN,
            "relative_volume_10d": BREAKOUT_MIN_RELATIVE_VOLUME,
            "volatility_adjusted_move": BREAKOUT_MIN_VOLATILITY_MOVE,
            "relative_spy": BREAKOUT_MIN_RELATIVE_SPY,
        },
        "coverage": {"requested": len(symbols), "evaluated": len(symbols) - unavailable, "unavailable": unavailable},
        "hits": hits,
        "metrics": metrics,
    }


def append_breakout_history(history: Any, report: dict[str, Any]) -> list[dict[str, Any]]:
    rows_by_date: dict[str, dict[str, Any]] = {}
    if isinstance(history, list):
        for row in history:
            if not isinstance(row, dict):
                continue
            date = row.get("model_date")
            if isinstance(date, str) and date:
                rows_by_date[date] = row
    model_date = report.get("model_date")
    if not isinstance(model_date, str) or not model_date:
        return [rows_by_date[key] for key in sorted(rows_by_date)][-520:]
    rows_by_date[model_date] = {
        "model_date": model_date,
        "coverage": copy.deepcopy(report.get("coverage")),
        "hits": copy.deepcopy(report.get("hits", [])),
    }
    return [rows_by_date[key] for key in sorted(rows_by_date)][-520:]


def choose_top10(rows: list[dict[str, Any]]) -> list[str]:
    selected: list[str] = []
    themes: set[str] = set()
    eligible = sorted(rows, key=lambda r: float(r.get("trend_score") or 0), reverse=True)
    for row in eligible:
        symbol = str(row.get("symbol") or "")
        theme = str(row.get("theme") or row.get("asset_type") or symbol)
        if symbol == "SGOV" or not symbol or theme in themes:
            continue
        if row.get("trade_state") in {"退出", "禁止追高"}:
            continue
        selected.append(symbol)
        themes.add(theme)
        if len(selected) == 10:
            break
    return selected


def freeze_snapshot(pool: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for r in pool.get("rows", []):
        rows.append({
            "symbol": r.get("symbol"), "theme": r.get("theme"),
            "score": r.get("trend_score"), "risk_score": r.get("trading_risk_score"),
            "state": r.get("trade_state"), "adjusted_close": r.get("adjusted_close"),
            "day_open": r.get("day_open"), "price": r.get("price"),
        })
    regime = str((pool.get("market_regime") or {}).get("state") or "震荡")
    return {
        "date": pool.get("model_date"), "created_at": datetime.now(timezone.utc).isoformat(),
        "universe": len(rows), "regime": regime, "exposure": exposure_for(regime),
        "top10": choose_top10(pool.get("rows", [])), "rows": rows, "outcomes": {},
    }


def mature(snapshots: list[dict[str, Any]]) -> None:
    for i, snap in enumerate(snapshots):
        base = {r["symbol"]: r for r in snap.get("rows", []) if r.get("symbol")}
        for h in HORIZONS:
            key = f"t{h}"
            if key in snap.get("outcomes", {}) or i + h >= len(snapshots):
                continue
            future = {r["symbol"]: r for r in snapshots[i + h].get("rows", []) if r.get("symbol")}
            symbols, scores, returns = [], [], []
            for symbol, row in base.items():
                b = float(row.get("adjusted_close") or 0)
                f = float((future.get(symbol) or {}).get("adjusted_close") or 0)
                score = row.get("score")
                if b > 0 and f > 0 and score is not None:
                    symbols.append(symbol); scores.append(float(score)); returns.append(f / b - 1)
            ic = spearman(scores, returns)
            ps, pr = percentile_ranks(scores), percentile_ranks(returns)
            deviation = statistics.fmean(abs(a - b) for a, b in zip(ps, pr)) if ps else None
            top = [returns[symbols.index(s)] for s in snap.get("top10", []) if s in symbols]
            spy_ret = returns[symbols.index("SPY")] if "SPY" in symbols else None
            snap.setdefault("outcomes", {})[key] = {
                "end_date": snapshots[i + h]["date"], "sample_count": len(symbols),
                "rank_ic": round(ic, 6) if ic is not None else None,
                "cross_sectional_deviation": round(deviation, 6) if deviation is not None else None,
                "top10_equal_return": round(statistics.fmean(top), 6) if top else None,
                "spy_return": round(spy_ret, 6) if spy_ret is not None else None,
            }


def turnover_cost(old: dict[str, float], new: dict[str, float]) -> float:
    return sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in set(old) | set(new)) * ONE_WAY_COST


def weights(symbols: list[str], exposure: float) -> dict[str, float]:
    return {s: exposure / len(symbols) for s in symbols} if symbols and exposure else {}


def shadow_portfolios(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    names = ("benchmark", "timing", "rotation", "fusion")
    equity = {n: INITIAL_CAPITAL for n in names}
    peaks = dict(equity); max_dd = {n: 0.0 for n in names}; old_w = {n: {} for n in names}
    history = []
    # Signal i is formed after close i, executed at open i+1, exited/rebalanced at open i+2.
    for i in range(len(snapshots) - 2):
        signal, entry, exit_ = snapshots[i], snapshots[i + 1], snapshots[i + 2]
        opens1 = {r["symbol"]: float(r.get("day_open") or 0) for r in entry.get("rows", []) if r.get("symbol")}
        opens2 = {r["symbol"]: float(r.get("day_open") or 0) for r in exit_.get("rows", []) if r.get("symbol")}
        rets = {s: opens2[s] / p - 1 for s, p in opens1.items() if p > 0 and opens2.get(s, 0) > 0}
        exp = float(signal.get("exposure") or 0)
        top = [s for s in signal.get("top10", []) if s in rets]
        target = {
            "benchmark": weights(["SPY"] if "SPY" in rets else [], 1.0),
            "timing": weights(["SPY"] if "SPY" in rets else [], exp),
            "rotation": weights(top, 1.0),
            "fusion": weights(top, exp),
        }
        daily = {}
        for name in names:
            gross = sum(w * rets.get(s, 0.0) for s, w in target[name].items())
            cost = turnover_cost(old_w[name], target[name])
            net = gross - cost
            equity[name] *= 1 + net
            peaks[name] = max(peaks[name], equity[name])
            max_dd[name] = min(max_dd[name], equity[name] / peaks[name] - 1)
            old_w[name] = target[name]
            daily[name] = round(net, 6)
        history.append({"signal_date": signal["date"], "entry_date": entry["date"], "exit_date": exit_["date"], "exposure": exp, "returns": daily})
    stats = {n: {"equity": round(equity[n], 2), "total_return": round(equity[n] / INITIAL_CAPITAL - 1, 6), "max_drawdown": round(max_dd[n], 6)} for n in names}
    return {"version": 1, "basis": EXECUTION_BASIS, "initial_capital_usd": INITIAL_CAPITAL, "one_way_cost": ONE_WAY_COST, "stats": stats, "history": history[-520:]}


def aggregate(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = {}
    for h in HORIZONS:
        vals = [s.get("outcomes", {}).get(f"t{h}") for s in snapshots]
        vals = [v for v in vals if v and v.get("rank_ic") is not None]
        ics = [float(v["rank_ic"]) for v in vals]
        devs = [float(v["cross_sectional_deviation"]) for v in vals if v.get("cross_sectional_deviation") is not None]
        metrics[f"t{h}"] = {
            "observations": len(vals), "rank_ic_mean": round(statistics.fmean(ics), 6) if ics else None,
            "rank_ic_positive_rate": round(sum(x > 0 for x in ics) / len(ics), 6) if ics else None,
            "deviation_mean": round(statistics.fmean(devs), 6) if devs else None,
            "random_deviation_reference": 0.333333,
        }
    return metrics


def main() -> None:
    pool = read_json(POOL, {})
    if not pool.get("model_date") or not pool.get("rows"):
        raise RuntimeError("US ETF pool snapshot is unavailable")
    payload = read_json(OUT, {"version": 1, "market": "US", "mode": "forward-only", "snapshots": []})
    snapshots = payload.get("snapshots", [])
    current = freeze_snapshot(pool)
    snapshots = [s for s in snapshots if s.get("date") != current["date"]]
    snapshots.append(current); snapshots.sort(key=lambda s: s["date"]); snapshots = snapshots[-520:]
    mature(snapshots)
    fingerprint = build_model_fingerprint(
        pool,
        horizons=HORIZONS,
        one_way_cost=ONE_WAY_COST,
        initial_capital=INITIAL_CAPITAL,
        execution_basis=EXECUTION_BASIS,
        exposure_mapping=EXPOSURE_MAPPING,
        default_exposure=DEFAULT_EXPOSURE,
    )
    payload.update({
        "updated_at": datetime.now(timezone.utc).isoformat(), "universe": current["universe"],
        "horizons": list(HORIZONS), "cost_assumption": {"one_way": ONE_WAY_COST},
        "metrics": aggregate(snapshots), "snapshots": snapshots,
        "model_fingerprint": copy.deepcopy(fingerprint),
        "note": "Forward-only self-evaluation. Cross-sectional deviation is monitored against the 1/3 random reference; AGRU is not active.",
    })
    previous_shadow = read_json(SHADOW, {})
    shadow = shadow_portfolios(snapshots)
    shadow["updated_at"] = payload["updated_at"]
    shadow["model_fingerprint"] = copy.deepcopy(fingerprint)
    if BAR_DB.exists():
        try:
            with sqlite3.connect(BAR_DB) as db:
                shadow["breakout_research"] = build_breakout_shadow_report(
                    db, model_date=str(current["date"]), pool_rows=pool.get("rows", []),
                )
        except (OSError, sqlite3.Error, ValueError, TypeError, ArithmeticError) as exc:
            shadow["breakout_research"] = {
                "version": 1, "mode": "shadow_research_only", "production_change_allowed": False,
                "model_date": current["date"], "status": "UNAVAILABLE",
                "reason": f"US ETF breakout research unavailable: {type(exc).__name__}",
            }
        if shadow["breakout_research"].get("coverage") is not None:
            shadow["breakout_history"] = append_breakout_history(
                previous_shadow.get("breakout_history", []), shadow["breakout_research"],
            )
    else:
        shadow["breakout_research"] = {
            "version": 1, "mode": "shadow_research_only", "production_change_allowed": False,
            "model_date": current["date"], "status": "UNAVAILABLE", "reason": "US ETF daily-bar cache is unavailable",
        }
    atomic_write(OUT, payload); atomic_write(SHADOW, shadow)
    print(json.dumps({"date": current["date"], "snapshots": len(snapshots), "top10": current["top10"], "exposure": current["exposure"], "metrics": payload["metrics"], "shadow_intervals": len(shadow["history"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
