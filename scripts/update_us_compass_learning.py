#!/usr/bin/env python3
"""Forward-only self-evaluation for the US ETF Compass.

Freezes each daily 74-ETF ranking, matures T+1/T+5/T+20 outcomes, computes
cross-sectional RankIC/deviation, and maintains four open-to-open shadow
portfolios. No brokerage/account access and no production-weight mutation.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "public/data/us-etf-pool.json"
OUT = ROOT / "public/data/us-compass-learning.json"
SHADOW = ROOT / "public/data/us-compass-shadow.json"
HORIZONS = (1, 5, 20)
INITIAL_CAPITAL = 20_000.0
ONE_WAY_COST = 0.001


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


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + j - 1) / 2 + 1
        for k in range(i, j):
            out[order[k]] = rank
        i = j
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((x - mx) * (y - my) for x, y in zip(rx, ry))
    dx = math.sqrt(sum((x - mx) ** 2 for x in rx))
    dy = math.sqrt(sum((y - my) ** 2 for y in ry))
    return num / (dx * dy) if dx and dy else None


def percentile_ranks(values: list[float]) -> list[float]:
    rr = ranks(values)
    n = len(values)
    return [((r - 1) / max(n - 1, 1)) for r in rr]


def exposure_for(regime: str) -> float:
    return {"偏强": 1.0, "震荡": 0.5, "防御": 0.0}.get(regime, 0.5)


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
    return {"version": 1, "basis": "T close signal; T+1 open execution; next-open rebalance", "initial_capital_usd": INITIAL_CAPITAL, "one_way_cost": ONE_WAY_COST, "stats": stats, "history": history[-520:]}


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
    payload.update({
        "updated_at": datetime.now(timezone.utc).isoformat(), "universe": current["universe"],
        "horizons": list(HORIZONS), "cost_assumption": {"one_way": ONE_WAY_COST},
        "metrics": aggregate(snapshots), "snapshots": snapshots,
        "note": "Forward-only self-evaluation. Cross-sectional deviation is monitored against the 1/3 random reference; AGRU is not active.",
    })
    shadow = shadow_portfolios(snapshots)
    shadow["updated_at"] = payload["updated_at"]
    atomic_write(OUT, payload); atomic_write(SHADOW, shadow)
    print(json.dumps({"date": current["date"], "snapshots": len(snapshots), "top10": current["top10"], "exposure": current["exposure"], "metrics": payload["metrics"], "shadow_intervals": len(shadow["history"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
