#!/usr/bin/env python3
"""Generate the US ETF Garden static datasets from Yahoo Chart API.

Trend/return calculations use adjusted close. Trigger prices use unadjusted OHLC.
The script writes atomically and preserves the last good snapshot on failure.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT_POOL = ROOT / "public/data/us-etf-pool.json"
OUT_GARDEN = ROOT / "public/data/us-etf-garden.json"
OUT_BACKTEST = ROOT / "public/data/us-etf-backtest.json"
NY = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 ETF-Garden/1.0"

UNIVERSE: list[tuple[str, str, str, str]] = [
    ("SPY", "SPDR S&P 500 ETF", "宽基", "美国宽基"), ("QQQ", "Invesco QQQ", "宽基", "科技成长"),
    ("DIA", "SPDR Dow Jones ETF", "宽基", "美国宽基"), ("IWM", "iShares Russell 2000", "宽基", "小盘股"),
    ("VTI", "Vanguard Total Stock Market", "宽基", "美国宽基"), ("RSP", "Invesco S&P 500 Equal Weight", "宽基", "美国宽基"),
    ("XLK", "Technology Select Sector", "行业", "科技"), ("XLF", "Financial Select Sector", "行业", "金融"),
    ("XLV", "Health Care Select Sector", "行业", "医疗"), ("XLE", "Energy Select Sector", "行业", "能源"),
    ("XLI", "Industrial Select Sector", "行业", "工业"), ("XLY", "Consumer Discretionary", "行业", "消费"),
    ("XLP", "Consumer Staples", "行业", "防御"), ("XLU", "Utilities Select Sector", "行业", "防御"),
    ("XLB", "Materials Select Sector", "行业", "原材料"), ("XLRE", "Real Estate Select Sector", "行业", "房地产"),
    ("XLC", "Communication Services", "行业", "通信"), ("SMH", "VanEck Semiconductor ETF", "主题", "半导体"),
    ("SOXX", "iShares Semiconductor ETF", "主题", "半导体"), ("IGV", "iShares Expanded Tech-Software", "主题", "软件"),
    ("CIBR", "First Trust Nasdaq Cybersecurity", "主题", "网络安全"), ("BOTZ", "Global X Robotics & AI", "主题", "人工智能"),
    ("ARKK", "ARK Innovation ETF", "主题", "创新成长"), ("IBB", "iShares Biotechnology ETF", "主题", "生物科技"),
    ("XBI", "SPDR S&P Biotech ETF", "主题", "生物科技"), ("ITA", "iShares U.S. Aerospace & Defense", "主题", "国防"),
    ("TAN", "Invesco Solar ETF", "主题", "新能源"), ("LIT", "Global X Lithium & Battery Tech", "主题", "新能源"),
    ("URA", "Global X Uranium ETF", "主题", "核能"), ("EFA", "iShares MSCI EAFE", "海外", "发达市场"),
    ("EEM", "iShares MSCI Emerging Markets", "海外", "新兴市场"), ("MCHI", "iShares MSCI China", "海外", "中国资产"),
    ("KWEB", "KraneShares CSI China Internet", "海外", "中国资产"), ("EWJ", "iShares MSCI Japan", "海外", "日本"),
    ("INDA", "iShares MSCI India", "海外", "印度"), ("EWZ", "iShares MSCI Brazil", "海外", "巴西"),
    ("TLT", "iShares 20+ Year Treasury Bond", "债券", "长债"), ("IEF", "iShares 7-10 Year Treasury Bond", "债券", "中债"),
    ("SHY", "iShares 1-3 Year Treasury Bond", "债券", "短债"), ("SGOV", "iShares 0-3 Month Treasury Bond", "现金", "现金替代"),
    ("GLD", "SPDR Gold Shares", "商品", "黄金"), ("SLV", "iShares Silver Trust", "商品", "贵金属"),
    ("USO", "United States Oil Fund", "商品", "原油"), ("DBC", "Invesco DB Commodity Index", "商品", "综合商品"),
    ("UUP", "Invesco DB US Dollar Bullish", "宏观", "美元"),
]


def finite(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def ret(values: list[float], days: int) -> float:
    return round((values[-1] / values[-1 - days] - 1) * 100, 2) if len(values) > days and values[-1 - days] else 0.0


def slope(values: list[float], n: int = 20) -> float:
    y = values[-n:]
    if len(y) < n: return 0.0
    xm = (n - 1) / 2; ym = statistics.fmean(y)
    denom = sum((i - xm) ** 2 for i in range(n))
    return sum((i - xm) * (v - ym) for i, v in enumerate(y)) / denom / ym * 100 if ym and denom else 0.0


def fetch(symbol: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d&events=div%2Csplits"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    rows = []
    for i, ts in enumerate(result["timestamp"]):
        vals = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
        adj = adjusted[i] if i < len(adjusted) else quote["close"][i]
        if any(v is None for v in vals[:4]) or adj is None: continue
        rows.append({"date": datetime.fromtimestamp(ts, timezone.utc).astimezone(NY).date().isoformat(), "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": vals[4] or 0, "adj": adj})
    if len(rows) < 80: raise RuntimeError(f"{symbol}: only {len(rows)} rows")
    return {"rows": rows, "meta": result.get("meta", {})}


def evaluate(item: tuple[str, str, str, str], data: dict[str, Any], spy_adj: list[float]) -> dict[str, Any]:
    symbol, name, asset_type, theme = item
    rows = data["rows"]; adj = [finite(x["adj"]) for x in rows]; raw = [finite(x["close"]) for x in rows]; vols = [finite(x["volume"]) for x in rows]
    ma20 = statistics.fmean(adj[-20:]); ma60 = statistics.fmean(adj[-60:]); ma20_prev = statistics.fmean(adj[-25:-5]);
    r3, r5, r20, r60 = (ret(adj, x) for x in (3, 5, 20, 60)); spy20 = ret(spy_adj, 20); spy60 = ret(spy_adj, 60)
    relative20, relative60 = round(r20 - spy20, 2), round(r60 - spy60, 2)
    vol_ratio = round(statistics.fmean(vols[-5:]) / max(statistics.fmean(vols[-20:]), 1), 2)
    last = rows[-1]; day_range = finite(last["high"]) - finite(last["low"]); close_pos = (finite(last["close"]) - finite(last["low"])) / day_range if day_range else .5
    distance_ma20 = (adj[-1] / ma20 - 1) * 100
    recent = adj[-20:]; max_dd = min((v / max(recent[:i+1]) - 1) * 100 for i, v in enumerate(recent))
    returns = [(adj[i] / adj[i-1] - 1) * 100 for i in range(max(1, len(adj)-20), len(adj))]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0
    trend_score = 50 + r5 * 1.5 + relative20 * 1.2 + relative60 * .35 + slope(adj, 20) * 8 + (8 if adj[-1] > ma20 else -12) + (6 if ma20 > ma20_prev else -8) + min((vol_ratio - 1) * 8, 8)
    trend_score = round(max(0, min(100, trend_score)), 1)
    risk_score = 15 + max(r3 - 4, 0) * 3 + max(r5 - 8, 0) * 2 + max(r20 - 20, 0) + max(distance_ma20 - 8, 0) * 2 + max(volatility - 1.5, 0) * 8 + abs(min(max_dd, 0)) * .6
    if close_pos < .3: risk_score += 10
    if vol_ratio < .75 and r3 > 2: risk_score += 8
    risk_score = round(max(0, min(100, risk_score)), 1)
    if r20 > 20:
        risk_score = max(risk_score, 42.0)
    if close_pos < .3 and r5 > 5:
        risk_score = max(risk_score, 35.0)
    strength = "A" if trend_score >= 75 else ("B" if trend_score >= 62 else ("C" if trend_score >= 50 else "D"))
    risk = "高" if risk_score >= 60 else ("中" if risk_score >= 35 else "低")
    momentum = adj[-1] > ma20 and ma20 > ma20_prev and r3 > -5
    if not momentum: state = "退出" if strength == "D" else "观察"
    elif risk == "高": state = "禁止追高"
    elif risk == "中": state = "回踩候选"
    elif strength in {"A", "B"}: state = "可持有"
    else: state = "观察"
    atr = statistics.fmean([finite(x["high"]) - finite(x["low"]) for x in rows[-14:]])
    support = round(max(ma20 * raw[-1] / adj[-1], raw[-1] - 1.5 * atr), 2)
    stop = round(min(ma20 * raw[-1] / adj[-1], raw[-1] - 2 * atr), 2)
    target = round(raw[-1] + 1.5 * atr, 2)
    flags = []
    if r20 > 20: flags.append("20日过热")
    if close_pos < .3: flags.append("收盘偏弱")
    if vol_ratio < .75: flags.append("量能不足")
    if max_dd < -8: flags.append("回撤偏大")
    if not momentum: flags.append("趋势未通过")
    return {
        "symbol": symbol, "name": name, "asset_type": asset_type, "theme": theme, "trade_date": rows[-1]["date"],
        "price": round(raw[-1], 2), "adjusted_close": round(adj[-1], 4), "ret3": r3, "ret5": r5, "ret20": r20, "ret60": r60,
        "relative_spy20": relative20, "relative_spy60": relative60, "ma20": round(ma20 * raw[-1] / adj[-1], 2), "ma60": round(ma60 * raw[-1] / adj[-1], 2),
        "volume_ratio": vol_ratio, "close_position": round(close_pos, 3), "max_drawdown20": round(max_dd, 2), "volatility20": round(volatility, 2),
        "trend_score": trend_score, "strength_level": strength, "trading_risk_score": risk_score, "risk_level": risk, "trade_state": state,
        "momentum_pass": momentum, "risk_flags": flags, "support": support, "target": target, "stop": stop,
        "trigger_price_basis": "raw_ohlc", "return_basis": "adjusted_close",
    }


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main() -> None:
    results: dict[str, dict[str, Any]] = {}; failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, item[0]): item[0] for item in UNIVERSE}
        for future in as_completed(futures):
            symbol = futures[future]
            try: results[symbol] = future.result()
            except Exception as exc: failures[symbol] = str(exc)
    if "SPY" not in results or len(results) < 40:
        raise RuntimeError(f"insufficient data: {len(results)}/{len(UNIVERSE)}, failures={failures}")
    spy_adj = [finite(x["adj"]) for x in results["SPY"]["rows"]]
    rows = [evaluate(item, results[item[0]], spy_adj) for item in UNIVERSE if item[0] in results]
    rows.sort(key=lambda x: x["trend_score"], reverse=True)
    spy = next(x for x in rows if x["symbol"] == "SPY")
    if spy["price"] > spy["ma20"] and spy["strength_level"] in {"A", "B"}: regime, equity = "偏强", "60%-80%"
    elif spy["price"] < spy["ma20"] and spy["strength_level"] == "D": regime, equity = "防御", "10%-30%"
    else: regime, equity = "震荡", "30%-50%"
    selected = []; themes = set()
    for row in rows:
        if row["trend_score"] < 55 or row["trade_state"] not in {"可持有", "回踩候选", "观察"} or row["symbol"] == "SGOV": continue
        if row["theme"] in themes: continue
        selected.append(row); themes.add(row["theme"])
        if len(selected) == 5: break
    now = datetime.now(NY); latest = max(x["trade_date"] for x in rows)
    pool_payload = {"market": "US", "model_version": "US ETF Garden v1", "generated_at": now.isoformat(), "model_date": latest, "quote_trade_date": latest, "timezone": "America/New_York", "data_source": "Yahoo Chart API", "market_regime": {"state": regime, "equity_allocation": equity, "benchmark": "SPY"}, "summary": {"universe": len(UNIVERSE), "valid": len(rows), "momentum_pass": sum(x["momentum_pass"] for x in rows)}, "recommendations": selected, "rows": rows, "failures": failures, "realtime_scope": ["当前价", "当日涨跌", "关键位触发"], "snapshot_scope": ["趋势分", "交易风险", "交易状态", "市场状态"]}
    garden_payload = {"market": "US", "date": latest, "updated_at": now.isoformat(), "stage": "美股收盘版", "summary": f"{regime}市场，权益参考{equity}；趋势强度与交易风险分开，不把强势等同于可追涨。", "market_regime": pool_payload["market_regime"], "recommendations": selected, "disclaimer": "个人研究记录，不构成投资建议。美股上涨用绿色、下跌用红色。"}
    # Lightweight historical directional validation: current universe, rolling MA20 signal, next-day return.
    evaluated = hits = 0
    for item in UNIVERSE:
        data = results.get(item[0]);
        if not data: continue
        adj = [finite(x["adj"]) for x in data["rows"]]
        for i in range(60, len(adj)-1):
            ma = statistics.fmean(adj[i-19:i+1]); prev = statistics.fmean(adj[i-24:i-4])
            if adj[i] > ma and ma > prev:
                evaluated += 1; hits += int(adj[i+1] >= adj[i])
    backtest_payload = {"market": "US", "generated_at": now.isoformat(), "basis": "adjusted_close", "benchmark": "SPY", "sample_count": evaluated, "next_day_direction_hits": hits, "next_day_direction_rate": round(hits/evaluated*100, 1) if evaluated else None, "note": "趋势通过后的次日方向验证，不等同于实际收益。"}
    atomic_write(OUT_POOL, pool_payload); atomic_write(OUT_GARDEN, garden_payload); atomic_write(OUT_BACKTEST, backtest_payload)
    print(json.dumps({"valid": len(rows), "failed": len(failures), "trade_date": latest, "regime": regime, "top": [x["symbol"] for x in selected], "backtest_samples": evaluated}, ensure_ascii=False))


if __name__ == "__main__": main()
