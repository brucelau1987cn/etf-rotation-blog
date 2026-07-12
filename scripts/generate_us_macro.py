#!/usr/bin/env python3
"""Generate the free, read-only US macro risk snapshot used by /us-garden/.

Sources: Yahoo Chart API for market proxies and FRED CSV for official macro series.
Failures never overwrite the last good snapshot.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public/data/us-macro-dashboard.json"
NY = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 ETF-Compass-Macro/1.0"


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def request_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def yahoo(symbol: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    result = request_json(url)["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    closes = []
    for stamp, value in zip(timestamps, quote.get("close", [])):
        if value is None:
            continue
        closes.append((datetime.fromtimestamp(stamp, timezone.utc).astimezone(NY).date().isoformat(), float(value)))
    if len(closes) < 6:
        raise RuntimeError(f"{symbol}: insufficient history")
    date, value = closes[-1]
    prev = closes[-2][1]
    week = closes[-6][1]
    return {
        "value": round(value, 4), "date": date,
        "change": round(value - prev, 4),
        "change_pct": round((value / prev - 1) * 100, 2) if prev else None,
        "change_5d_pct": round((value / week - 1) * 100, 2) if week else None,
        "source": "Yahoo Chart API", "symbol": symbol,
    }


def fred(series: str) -> dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as response:
        text = response.read().decode("utf-8", "replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = row.get(series)
        if raw in (None, "", "."):
            continue
        try:
            rows.append((row["DATE"], float(raw)))
        except (KeyError, ValueError):
            continue
    if len(rows) < 2:
        raise RuntimeError(f"{series}: insufficient observations")
    date, value = rows[-1]
    previous = rows[-2][1]
    return {"value": round(value, 4), "date": date, "change": round(value - previous, 4), "source": "FRED", "series": series}


def level(score: int) -> tuple[str, str]:
    if score >= 7: return "danger", "危险"
    if score >= 5: return "tight", "紧张"
    if score >= 3: return "slightly-tight", "略紧"
    return "loose", "低风险"


def main() -> None:
    failures: dict[str, str] = {}
    market: dict[str, dict[str, Any]] = {}
    official: dict[str, dict[str, Any]] = {}
    for key, symbol in {
        "vix": "^VIX", "move": "^MOVE", "yield_10y_proxy": "^TNX", "yield_2y_proxy": "2YY=F",
        "spy": "SPY", "rsp": "RSP", "hyg": "HYG", "lqd": "LQD",
        "dollar": "DX-Y.NYB", "oil": "USO", "gold": "GLD", "copper": "COPX",
    }.items():
        try: market[key] = yahoo(symbol)
        except Exception as exc: failures[key] = str(exc)
    fred_available = True
    fred_series = {
        "yield_2y": "DGS2", "yield_10y": "DGS10", "curve_10y2y": "T10Y2Y", "sofr": "SOFR",
        "fed_assets": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD",
        "cpi": "CPIAUCSL", "core_pce": "PCEPILFE", "unemployment": "UNRATE", "payrolls": "PAYEMS",
    }
    for key, series in fred_series.items():
        if not fred_available:
            failures[key] = "FRED本轮不可达，已跳过"
            continue
        try: official[key] = fred(series)
        except Exception as exc:
            failures[key] = str(exc)
            if isinstance(exc, (TimeoutError, urllib.error.URLError)):
                fred_available = False

    def val(group: dict[str, dict[str, Any]], key: str) -> float | None:
        item = group.get(key)
        return float(item["value"]) if item and math.isfinite(float(item["value"])) else None

    vix = val(market, "vix")
    y10 = val(official, "yield_10y") or val(market, "yield_10y_proxy")
    y2 = val(official, "yield_2y") or val(market, "yield_2y_proxy")
    curve = val(official, "curve_10y2y")
    if curve is None and y10 is not None and y2 is not None:
        curve = round(y10 - y2, 4)
    score = 0
    notes = []
    if vix is not None:
        score += 4 if vix >= 30 else 3 if vix >= 25 else 2 if vix >= 20 else 0
        notes.append(f"VIX {vix:.1f}")
    if y10 is not None:
        score += 2 if y10 >= 5 else 1 if y10 >= 4.5 else 0
        notes.append(f"10Y {y10:.2f}%")
    if curve is not None and curve < 0:
        score += 1
    hyg = market.get("hyg", {}).get("change_5d_pct")
    lqd = market.get("lqd", {}).get("change_5d_pct")
    credit_relative = round(float(hyg) - float(lqd), 2) if hyg is not None and lqd is not None else None
    if credit_relative is not None and credit_relative <= -1.5:
        score += 2
    spy = val(market, "spy"); rsp = val(market, "rsp")
    breadth_relative = None
    if market.get("spy", {}).get("change_5d_pct") is not None and market.get("rsp", {}).get("change_5d_pct") is not None:
        breadth_relative = round(float(market["rsp"]["change_5d_pct"]) - float(market["spy"]["change_5d_pct"]), 2)
    risk_key, risk_label = level(score)

    dimensions = [
        {
            "key": "volatility", "title": "波动率", "state": "危险" if vix and vix >= 30 else "升温" if vix and vix >= 20 else "稳定",
            "tone": "danger" if vix and vix >= 30 else "warning" if vix and vix >= 20 else "positive",
            "headline": f"VIX {vix:.1f}" if vix is not None else "VIX N/A",
            "detail": f"日变动 {market.get('vix', {}).get('change_pct', '—')}% · 股票波动风险",
            "impact": "高Beta ETF减仓优先" if vix and vix >= 25 else "不额外限制正常持仓",
            "symbols": ["SPY", "QQQ", "IWM", "ARKK"], "as_of": market.get("vix", {}).get("date"),
        },
        {
            "key": "rates", "title": "利率与曲线", "state": "承压" if y10 and y10 >= 4.5 else "中性",
            "tone": "warning" if y10 and y10 >= 4.5 else "neutral",
            "headline": f"10Y {y10:.2f}%" if y10 is not None else "10Y N/A",
            "detail": f"2Y {y2:.2f}% · 2s10s {curve:+.2f}%" if y2 is not None and curve is not None else "收益率数据不完整",
            "impact": "成长ETF禁止追高" if y10 and y10 >= 4.5 else "估值压力暂不升级",
            "symbols": ["QQQ", "XLK", "SMH", "TLT"], "as_of": (official.get("yield_10y") or market.get("yield_10y_proxy") or {}).get("date"),
        },
        {
            "key": "liquidity", "title": "资金与流动性", "state": "数据待更新" if val(official, "sofr") is None else "观察", "tone": "missing" if val(official, "sofr") is None else "neutral",
            "headline": f"SOFR {val(official, 'sofr'):.2f}%" if val(official, "sofr") is not None else "官方数据待更新",
            "detail": "FRED本轮不可达；不对缺失数据作风险判断" if val(official, "sofr") is None else "Fed资产 / TGA / ON RRP为低频公开数据",
            "impact": "本维度暂不计入风险分" if val(official, "sofr") is None else "仅作仓位闸门，不作盘中触发",
            "symbols": ["SPY", "QQQ", "TLT"], "as_of": official.get("sofr", {}).get("date"),
        },
        {
            "key": "credit", "title": "信用与广度", "state": "收缩" if credit_relative is not None and credit_relative < -1 else "稳定",
            "tone": "warning" if credit_relative is not None and credit_relative < -1 else "positive",
            "headline": f"HYG/LQD 5日 {credit_relative:+.2f}pp" if credit_relative is not None else "HYG/LQD N/A",
            "detail": f"RSP相对SPY 5日 {breadth_relative:+.2f}pp" if breadth_relative is not None else "等权广度数据不完整",
            "impact": "小盘与高Beta降低仓位" if credit_relative is not None and credit_relative < -1 else "信用风险未明显扩散",
            "symbols": ["IWM", "XBI", "ARKK", "KRE"], "as_of": market.get("hyg", {}).get("date"),
        },
    ]

    impacts = []
    if y10 is not None:
        impacts.append({"driver": "10Y收益率偏高" if y10 >= 4.5 else "利率压力温和", "benefit": ["XLF" if y10 >= 4.5 else "QQQ"], "pressure": ["QQQ", "ARKK", "TLT"] if y10 >= 4.5 else ["UUP"], "discipline": "成长方向只等伏击位，不追高" if y10 >= 4.5 else "不改变正常伏击纪律"})
    if credit_relative is not None:
        impacts.append({"driver": "信用风险收缩" if credit_relative < -1 else "信用环境稳定", "benefit": ["TLT", "GLD"] if credit_relative < -1 else ["IWM", "XBI"], "pressure": ["IWM", "ARKK", "KRE"] if credit_relative < -1 else [], "discipline": "高Beta仓位减半" if credit_relative < -1 else "维持正常仓位上限"})
    if breadth_relative is not None:
        impacts.append({"driver": "等权落后" if breadth_relative < -1 else "上涨扩散", "benefit": ["SPY", "QQQ"] if breadth_relative < -1 else ["RSP", "IWM"], "pressure": ["RSP", "IWM"] if breadth_relative < -1 else [], "discipline": "警惕指数强、内部弱" if breadth_relative < -1 else "轮动参与度改善"})

    now = datetime.now(NY)
    events = [
        {"date": "2026-07-28", "end_date": "2026-07-29", "time_et": "14:00", "title": "FOMC利率决议", "importance": "高", "tone": "danger", "symbols": ["SPY", "QQQ", "TLT", "XLF"], "discipline": "决议前不追高，保留现金应对波动", "source": "Federal Reserve"},
    ]
    events = [event for event in events if event["end_date"] >= now.date().isoformat()]
    payload = {
        "version": 1, "generated_at": now.isoformat(), "timezone": "America/New_York",
        "risk": {"key": risk_key, "label": risk_label, "score": score, "headline": " · ".join(notes[:2]) or "核心数据暂缺", "equity_constraint": "暂停新增伏击" if score >= 7 else "新增伏击减半" if score >= 5 else "禁止追高、按关键位执行" if score >= 3 else "允许正常伏击与持仓"},
        "dimensions": dimensions, "impacts": impacts[:3],
        "events": events,
        "market": market, "official": official,
        "data_quality": {"failed": len(failures), "failures": failures, "note": "免费公开源；不同序列频率不同，卡片显示各自观察日期。"},
        "sources": ["Yahoo Chart API", "Federal Reserve Economic Data (FRED)"],
    }
    if len(dimensions) < 4 or (not market and not official):
        raise RuntimeError("insufficient macro data")
    atomic_write(OUTPUT, payload)
    print(json.dumps({"risk": risk_label, "score": score, "market": len(market), "official": len(official), "failed": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
