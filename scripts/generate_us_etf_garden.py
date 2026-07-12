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
OUT_FLOWER_HISTORY = ROOT / "public/data/us-etf-flower-history.json"
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
    # 行业深度扩展池：参与模型，但同主题最多一只进入Top观察池。
    ("XSD", "SPDR S&P Semiconductor ETF", "主题", "半导体"),
    ("FDN", "First Trust Dow Jones Internet", "主题", "互联网"),
    ("SKYY", "First Trust Cloud Computing", "主题", "云计算"),
    ("HACK", "Amplify Cybersecurity ETF", "主题", "网络安全"),
    ("XRT", "SPDR S&P Retail ETF", "行业", "零售"),
    ("ITB", "iShares U.S. Home Construction", "行业", "房屋建筑"),
    ("DRIV", "Global X Autonomous & Electric Vehicles", "主题", "智能汽车"),
    ("IHI", "iShares U.S. Medical Devices", "行业", "医疗器械"),
    ("IHF", "iShares U.S. Healthcare Providers", "行业", "医疗服务"),
    ("KRE", "SPDR S&P Regional Banking", "行业", "区域银行"),
    ("KIE", "SPDR S&P Insurance", "行业", "保险"),
    ("XAR", "SPDR S&P Aerospace & Defense", "主题", "国防"),
    ("IYT", "iShares Transportation Average", "行业", "运输"),
    ("PAVE", "Global X U.S. Infrastructure Development", "主题", "基础设施"),
    ("XOP", "SPDR S&P Oil & Gas Exploration & Production", "行业", "油气勘探"),
    ("OIH", "VanEck Oil Services", "行业", "油服"),
    ("ICLN", "iShares Global Clean Energy", "主题", "清洁能源"),
    ("XME", "SPDR S&P Metals & Mining", "行业", "金属矿业"),
    ("GDX", "VanEck Gold Miners", "行业", "黄金矿业"),
    ("COPX", "Global X Copper Miners", "行业", "铜矿"),
    ("VNQ", "Vanguard Real Estate ETF", "行业", "房地产"),
    ("XHB", "SPDR S&P Homebuilders", "行业", "房屋建筑"),
]

BREADTH_GROUPS: dict[str, list[str]] = {
    "科技": ["XLK", "QQQ", "IGV", "FDN", "SKYY"],
    "半导体": ["SMH", "SOXX", "XSD"],
    "网络安全": ["CIBR", "HACK"],
    "医疗": ["XLV", "IHI", "IHF", "XBI", "IBB"],
    "金融": ["XLF", "KRE", "KIE"],
    "能源": ["XLE", "XOP", "OIH"],
    "国防": ["ITA", "XAR"],
    "房屋建筑": ["ITB", "XHB"],
    "新能源": ["TAN", "ICLN", "LIT"],
    "材料矿业": ["XLB", "XME", "GDX", "COPX"],
    "房地产": ["XLRE", "VNQ"],
}


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
        "price": round(raw[-1], 2), "day_high": round(finite(last["high"]), 2), "day_low": round(finite(last["low"]), 2), "adjusted_close": round(adj[-1], 4), "ret3": r3, "ret5": r5, "ret20": r20, "ret60": r60,
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


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def flower_signals(rows: list[dict[str, Any]], previous: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build ready/triggered flower signals; triggers use prior snapshot levels."""
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in ("ready_plant", "plant", "ready_harvest", "harvest", "exit")}
    for row in rows:
        if row["symbol"] == "SGOV":
            continue
        support_gap = round((row["price"] / row["support"] - 1) * 100, 2) if row["support"] else 999
        target_gap = round((row["target"] / row["price"] - 1) * 100, 2) if row["price"] else 999
        keys = ("symbol", "name", "theme", "price", "support", "target", "stop", "strength_level", "trend_score", "risk_level", "trade_state", "ret20", "relative_spy20")
        base = {k: row[k] for k in keys}
        base.update({"support_gap": support_gap, "target_gap": target_gap, "trade_date": row["trade_date"]})
        old = previous.get(row["symbol"])
        old_target_gap = 999.0
        if old:
            old_target_gap = round((finite(old.get("target")) / row["price"] - 1) * 100, 2) if row["price"] else 999
            if row["day_low"] <= finite(old.get("stop")):
                buckets["exit"].append({**base, "signal": "失效退出", "trigger_level": old["stop"], "trigger_basis": "当日最低价≤前一快照失效线"})
            elif row["day_high"] >= finite(old.get("target")):
                buckets["harvest"].append({**base, "signal": "摘花", "trigger_level": old["target"], "trigger_basis": "当日最高价≥前一快照目标位"})
            elif row["day_low"] <= finite(old.get("support")) and row["price"] > finite(old.get("stop")) and row["momentum_pass"] and row["risk_level"] != "高":
                buckets["plant"].append({**base, "signal": "种花", "trigger_level": old["support"], "trigger_basis": "当日最低价≤前一快照回踩位且未失效"})
        if row["momentum_pass"] and row["risk_level"] != "高" and row["trade_state"] in {"可持有", "回踩候选"} and 0 <= support_gap <= 3:
            buckets["ready_plant"].append({**base, "signal": "准备种花", "trigger_level": row["support"], "trigger_basis": "距回踩位3%以内"})
        if (old and 0 <= old_target_gap <= 3 and row["momentum_pass"]) or (row["ret20"] >= 15 and row["risk_level"] in {"中", "高"}):
            buckets["ready_harvest"].append({**base, "signal": "准备摘花", "trigger_level": row["target"], "trigger_basis": "距目标3%以内或20日过热"})
    for key in ("ready_plant", "ready_harvest"):
        sort_key = "support_gap" if key == "ready_plant" else "target_gap"
        buckets[key].sort(key=lambda x: (x[sort_key], -x["trend_score"]))
        seen: set[str] = set(); deduped = []
        for item in buckets[key]:
            if item["theme"] in seen:
                continue
            deduped.append(item); seen.add(item["theme"])
            if len(deduped) == 8:
                break
        buckets[key] = deduped
    for key in ("plant", "harvest", "exit"):
        buckets[key].sort(key=lambda x: x["symbol"])
    return buckets


def main() -> None:
    prior_pool = read_json(OUT_POOL, {})
    previous_rows = {r["symbol"]: r for r in prior_pool.get("rows", [])}
    results: dict[str, dict[str, Any]] = {}; failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, item[0]): item[0] for item in UNIVERSE}
        for future in as_completed(futures):
            symbol = futures[future]
            try: results[symbol] = future.result()
            except Exception as exc: failures[symbol] = str(exc)
    if "SPY" not in results or len(results) < 60:
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
    row_map = {row["symbol"]: row for row in rows}
    breadth = []
    for group, symbols in BREADTH_GROUPS.items():
        members = [row_map[s] for s in symbols if s in row_map]
        passed = sum(bool(r["momentum_pass"]) for r in members)
        ratio = round(passed / len(members) * 100, 1) if members else 0.0
        avg_relative = round(statistics.fmean(r["relative_spy20"] for r in members), 2) if members else 0.0
        if ratio >= 80:
            breadth_state = "全面扩散" if avg_relative > 0 else "同步通过·相对偏弱"
        elif ratio >= 60:
            breadth_state = "多数确认" if avg_relative > 0 else "多数通过·相对偏弱"
        else:
            breadth_state = "局部走强" if ratio > 0 else "整体偏弱"
        breadth.append({
            "group": group, "members": [r["symbol"] for r in members], "passed": passed,
            "total": len(members), "ratio": ratio,
            "avg_relative_spy20": avg_relative,
            "state": breadth_state,
        })
    breadth.sort(key=lambda x: (x["ratio"], x["avg_relative_spy20"]), reverse=True)
    trigger_previous = previous_rows if prior_pool.get("model_date", "") < latest else {}
    flowers = flower_signals(rows, trigger_previous)
    flower_counts = {key: len(value) for key, value in flowers.items()}
    pool_payload = {"market": "US", "model_version": "US ETF Garden v3 · Flower Signals", "generated_at": now.isoformat(), "model_date": latest, "quote_trade_date": latest, "timezone": "America/New_York", "data_source": "Yahoo Chart API", "market_regime": {"state": regime, "equity_allocation": equity, "benchmark": "SPY"}, "summary": {"universe": len(UNIVERSE), "valid": len(rows), "momentum_pass": sum(x["momentum_pass"] for x in rows)}, "recommendations": selected, "flower_signals": flowers, "flower_counts": flower_counts, "breadth_groups": breadth, "rows": rows, "failures": failures, "realtime_scope": ["当前价", "当日涨跌", "关键位触发"], "snapshot_scope": ["趋势分", "交易风险", "交易状态", "市场状态", "行业广度", "准备信号"]}
    garden_payload = {"market": "US", "date": latest, "updated_at": now.isoformat(), "stage": "美股收盘版", "model_version": pool_payload["model_version"], "data_source": pool_payload["data_source"], "summary": f"{regime}市场，权益参考{equity}；种花/摘花触发使用前一快照关键位，准备信号按当前关键位计算。", "market_regime": pool_payload["market_regime"], "recommendations": selected, "flower_signals": flowers, "flower_counts": flower_counts, "breadth_groups": breadth, "disclaimer": "个人研究记录，不构成投资建议。美股上涨用绿色、下跌用红色。"}
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
    history_payload = read_json(OUT_FLOWER_HISTORY, {"market": "US", "version": 1, "records": []})
    history_record = {"date": latest, "generated_at": now.isoformat(), "market_regime": pool_payload["market_regime"], "counts": flower_counts, "signals": flowers}
    history_payload["records"] = [x for x in history_payload.get("records", []) if x.get("date") != latest]
    history_payload["records"].append(history_record)
    history_payload["records"] = sorted(history_payload["records"], key=lambda x: x["date"], reverse=True)[:260]
    history_payload["updated_at"] = now.isoformat()
    atomic_write(OUT_POOL, pool_payload); atomic_write(OUT_GARDEN, garden_payload); atomic_write(OUT_BACKTEST, backtest_payload); atomic_write(OUT_FLOWER_HISTORY, history_payload)
    macro_status = "ok"
    try:
        from generate_us_macro import main as generate_us_macro
        generate_us_macro()
    except Exception as exc:
        # Macro refresh is a sidecar: retain its last good snapshot without failing the trading dashboard.
        macro_status = f"retained: {type(exc).__name__}"
    print(json.dumps({"valid": len(rows), "failed": len(failures), "trade_date": latest, "regime": regime, "top": [x["symbol"] for x in selected], "flowers": flower_counts, "backtest_samples": evaluated, "macro": macro_status}, ensure_ascii=False))


if __name__ == "__main__": main()
