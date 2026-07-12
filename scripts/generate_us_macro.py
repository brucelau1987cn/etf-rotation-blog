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
import re
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public/data/us-macro-dashboard.json"
NY = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 ETF-Compass-Macro/1.0"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

FRED_META = {
    "DGS2": ("日频", "%"), "DGS10": ("日频", "%"), "DGS30": ("日频", "%"),
    "T10Y2Y": ("日频", "%"), "SOFR": ("日频", "%"),
    "WALCL": ("周频", "百万美元"), "WTREGEN": ("周频", "百万美元"),
    "RRPONTSYD": ("日频", "十亿美元"), "CPIAUCSL": ("月频", "指数"),
    "CPILFESL": ("月频", "指数"), "PCEPILFE": ("月频", "指数"),
    "UNRATE": ("月频", "%"), "PAYEMS": ("月频", "千人"),
    "SAHMREALTIME": ("月频", "百分点"), "RRSFS": ("月频", "百万实际美元"),
    "GDPNOW": ("季频滚动更新", "% SAAR"),
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


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
    observation_date, value = rows[-1]
    previous = rows[-2][1]
    frequency, unit = FRED_META.get(series, ("按来源", ""))
    result = {
        "value": round(value, 4), "date": observation_date, "change": round(value - previous, 4),
        "previous": round(previous, 4), "source": "FRED", "series": series,
        "frequency": frequency, "unit": unit, "stale": False,
    }
    if len(rows) >= 13 and frequency == "月频":
        year_ago = rows[-13][1]
        result["change_yoy_pct"] = round((value / year_ago - 1) * 100, 2) if year_ago else None
    if len(rows) >= 4 and frequency == "月频":
        three_month = rows[-4][1]
        result["change_3m_pct"] = round((value / three_month - 1) * 100, 2) if three_month else None
    return result


def fomc_events(today: date) -> list[dict[str, Any]]:
    req = urllib.request.Request(FOMC_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor()
    parser.feed(html)
    text = " ".join(parser.parts)
    marker = f"{today.year} FOMC Meetings"
    if marker not in text:
        raise RuntimeError(f"FOMC calendar missing {today.year}")
    section = text.split(marker, 1)[1].split(f"{today.year - 1} FOMC Meetings", 1)[0]
    months = {name: number for number, name in enumerate(
        ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    )}
    found: list[dict[str, Any]] = []
    for month, start_raw, end_raw in re.findall(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:-(\d{1,2}))?\*?",
        section,
    ):
        start_day = int(start_raw)
        end_day = int(end_raw or start_raw)
        start = date(today.year, months[month], start_day)
        end = date(today.year, months[month], end_day)
        if end < today:
            continue
        found.append({
            "date": end.isoformat(), "start_date": start.isoformat(), "end_date": end.isoformat(), "time_et": "14:00",
            "title": "FOMC利率决议", "importance": "高", "tone": "warning",
            "symbols": ["SPY", "QQQ", "TLT", "XLF"],
            "discipline": "决议前不追高，保留现金应对波动",
            "source": "Federal Reserve", "source_url": FOMC_URL,
        })
    if not found:
        raise RuntimeError("no future FOMC meeting parsed")
    return found[:4]


def bls_fallback(today: date) -> dict[str, dict[str, Any]]:
    series_map = {"unemployment": "LNS14000000", "payrolls": "CES0000000001", "cpi": "CUUR0000SA0", "core_cpi": "CUUR0000SA0L1E"}
    body = json.dumps({"seriesid": list(series_map.values()), "startyear": str(today.year - 1), "endyear": str(today.year)}).encode()
    request = urllib.request.Request("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS status {payload.get('status')}")
    reverse = {value: key for key, value in series_map.items()}
    output: dict[str, dict[str, Any]] = {}
    unemployment_rows: list[tuple[str, float]] = []
    for block in payload["Results"]["series"]:
        key = reverse.get(block.get("seriesID"))
        if not key:
            continue
        rows = []
        for item in block.get("data", []):
            if not re.fullmatch(r"M\d{2}", item.get("period", "")):
                continue
            try:
                rows.append((f"{item['year']}-{item['period'][1:]}-01", float(item["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        if len(rows) < 2:
            continue
        observation_date, value = rows[-1]; previous = rows[-2][1]
        unit = "%" if key == "unemployment" else "千人" if key == "payrolls" else "指数"
        result = {"value": value, "date": observation_date, "change": round(value - previous, 4), "previous": previous,
                  "source": "U.S. Bureau of Labor Statistics", "series": block["seriesID"], "frequency": "月频", "unit": unit, "stale": False}
        if len(rows) >= 13 and key in {"cpi", "core_cpi"}:
            result["change_yoy_pct"] = round((value / rows[-13][1] - 1) * 100, 2)
        output[key] = result
        if key == "unemployment":
            unemployment_rows = rows
    if len(unemployment_rows) >= 15:
        averages = [(unemployment_rows[i][0], sum(value for _, value in unemployment_rows[i-2:i+1]) / 3) for i in range(2, len(unemployment_rows))]
        current_date, current_average = averages[-1]; prior_low = min(value for _, value in averages[-13:-1])
        current_sahm = current_average - prior_low
        previous_sahm = averages[-2][1] - min(value for _, value in averages[-14:-2]) if len(averages) >= 14 else None
        output["sahm"] = {"value": round(current_sahm, 2), "date": current_date, "change": round(current_sahm - previous_sahm, 2) if previous_sahm is not None else None,
                          "source": "BLS unemployment · calculated Sahm rule", "series": "LNS14000000", "frequency": "月频", "unit": "百分点", "stale": False}
    return output


def treasury_fallback(today: date) -> dict[str, dict[str, Any]]:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{today.year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={today.year}&page&_format=csv")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8", "replace"))))
    rows = [row for row in rows if row.get("Date")]; rows.sort(key=lambda row: datetime.strptime(row["Date"], "%m/%d/%Y"))
    if len(rows) < 2:
        raise RuntimeError("Treasury yield curve has insufficient rows")
    output = {}
    for key, column in {"yield_2y": "2 Yr", "yield_10y": "10 Yr", "yield_30y": "30 Yr"}.items():
        value = float(rows[-1][column]); previous = float(rows[-2][column])
        output[key] = {"value": value, "date": datetime.strptime(rows[-1]["Date"], "%m/%d/%Y").date().isoformat(), "change": round(value - previous, 4),
                       "previous": previous, "source": "U.S. Department of the Treasury", "series": column, "frequency": "日频", "unit": "%", "stale": False}
    curve = output["yield_10y"]["value"] - output["yield_2y"]["value"]
    previous_curve = output["yield_10y"]["previous"] - output["yield_2y"]["previous"]
    output["curve_10y2y"] = {"value": round(curve, 4), "date": output["yield_10y"]["date"],
                              "change": round(curve - previous_curve, 4), "source": "U.S. Department of the Treasury", "series": "10Y-2Y", "frequency": "日频", "unit": "%", "stale": False}
    return output


def gdpnow_fallback() -> dict[str, Any]:
    url = "https://www.atlantafed.org/research-and-data/data/gdpnow"
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor(); parser.feed(html); text = " ".join(parser.parts)
    value_match = re.search(r"([-+]?\d+(?:\.\d+)?)%\s+Latest GDPNow Estimate", text)
    date_match = re.search(r"Updated:\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    if not value_match or not date_match:
        raise RuntimeError("GDPNow value or update date not found")
    return {"value": float(value_match.group(1)), "date": datetime.strptime(date_match.group(1), "%B %d, %Y").date().isoformat(), "change": 0,
            "source": "Federal Reserve Bank of Atlanta", "series": "GDPNow", "frequency": "季频滚动更新", "unit": "% SAAR", "stale": False}


def level(score: int) -> tuple[str, str]:
    if score >= 7: return "danger", "危险"
    if score >= 5: return "tight", "紧张"
    if score >= 3: return "slightly-tight", "略紧"
    return "loose", "低风险"


def main() -> None:
    failures: dict[str, str] = {}
    market: dict[str, dict[str, Any]] = {}
    official: dict[str, dict[str, Any]] = {}
    now = datetime.now(NY)
    previous_snapshot = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    for key, symbol in {
        "vix": "^VIX", "move": "^MOVE", "yield_10y_proxy": "^TNX", "yield_2y_proxy": "2YY=F",
        "spy": "SPY", "rsp": "RSP", "hyg": "HYG", "lqd": "LQD",
        "dollar": "DX-Y.NYB", "oil": "USO", "gold": "GLD", "copper": "COPX",
    }.items():
        try: market[key] = yahoo(symbol)
        except Exception as exc: failures[key] = str(exc)
    for source_key, loader in {
        "bls": lambda: bls_fallback(now.date()),
        "treasury": lambda: treasury_fallback(now.date()),
    }.items():
        try:
            official.update(loader())
        except Exception as exc:
            failures[source_key] = str(exc)
    try:
        official["gdpnow"] = gdpnow_fallback()
    except Exception as exc:
        failures["gdpnow_atlanta"] = str(exc)

    fred_available = True
    fred_series = {
        "yield_2y": "DGS2", "yield_10y": "DGS10", "yield_30y": "DGS30", "curve_10y2y": "T10Y2Y", "sofr": "SOFR",
        "fed_assets": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD",
        "cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "core_pce": "PCEPILFE",
        "unemployment": "UNRATE", "payrolls": "PAYEMS", "sahm": "SAHMREALTIME",
        "real_retail": "RRSFS", "gdpnow": "GDPNOW",
    }
    for key, series in fred_series.items():
        if key in official:
            continue
        if not fred_available:
            failures[key] = "FRED本轮不可达；等待下轮更新"
            continue
        try:
            official[key] = fred(series)
        except Exception as exc:
            failures[key] = str(exc)
            if isinstance(exc, (TimeoutError, urllib.error.URLError)):
                fred_available = False
    for key, item in previous_snapshot.get("official", {}).items():
        if key not in official:
            official[key] = {**item, "stale": True}
    for key, item in previous_snapshot.get("market", {}).items():
        if key not in market:
            market[key] = {**item, "stale": True}

    def val(group: dict[str, dict[str, Any]], key: str) -> float | None:
        item = group.get(key)
        if item and item.get("stale"):
            return None
        return float(item["value"]) if item and math.isfinite(float(item["value"])) else None

    vix = val(market, "vix")
    y10 = val(official, "yield_10y") or val(market, "yield_10y_proxy")
    y2 = val(official, "yield_2y") or val(market, "yield_2y_proxy")
    y30 = val(official, "yield_30y")
    curve = val(official, "curve_10y2y")
    if curve is None and y10 is not None and y2 is not None:
        curve = round(y10 - y2, 4)
    curve_10y30y = y30 - y10 if y10 is not None and y30 is not None else None
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
            "detail": f"2Y {y2:.2f}% · 30Y {y30:.2f}% · 2s10s {curve:+.2f}% · 10s30s {curve_10y30y:+.2f}%" if y2 is not None and y30 is not None and curve is not None and curve_10y30y is not None else "收益率数据不完整",
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

    def fundamental(key: str, title: str, formatter: str, detail: str) -> dict[str, Any]:
        item = official.get(key)
        if not item:
            return {"key": key, "title": title, "value": "数据待更新", "detail": detail, "as_of": None, "frequency": "—", "source": "FRED", "tone": "missing"}
        try:
            display_value = formatter.format(**item)
        except (KeyError, TypeError, ValueError):
            display_value = f"{item.get('value', '—')} {item.get('unit', '')}".strip()
        return {
            "key": key, "title": title, "value": display_value, "detail": detail,
            "as_of": item.get("date"), "frequency": item.get("frequency", "按来源"),
            "source": item.get("source", "FRED"), "tone": "warning" if item.get("stale") else "neutral",
            "stale": bool(item.get("stale")),
        }

    fundamentals = [
        fundamental("sahm", "萨姆规则", "{value:.2f}pp", "≥0.50pp才触发衰退信号"),
        fundamental("unemployment", "失业率", "{value:.1f}%", "就业温度计，不用单月波动机械交易"),
        fundamental("payrolls", "非农就业", "{change:+.0f}千人", "较前月就业人数变化"),
        fundamental("core_cpi", "核心CPI", "同比 {change_yoy_pct:.2f}%", "剔除食品与能源后的价格趋势"),
        fundamental("core_pce", "核心PCE", "同比 {change_yoy_pct:.2f}%", "美联储重点通胀口径"),
        fundamental("real_retail", "实际零售销售", "3月 {change_3m_pct:+.2f}%", "消费动能的三个月变化"),
        fundamental("gdpnow", "GDPNow", "{value:.2f}%", "当前季度实际GDP年化即时估计"),
    ]

    impacts = []
    if y10 is not None:
        impacts.append({"driver": "10Y收益率偏高" if y10 >= 4.5 else "利率压力温和", "benefit": ["XLF" if y10 >= 4.5 else "QQQ"], "pressure": ["QQQ", "ARKK", "TLT"] if y10 >= 4.5 else ["UUP"], "discipline": "成长方向只等伏击位，不追高" if y10 >= 4.5 else "不改变正常伏击纪律"})
    if credit_relative is not None:
        impacts.append({"driver": "信用风险收缩" if credit_relative < -1 else "信用环境稳定", "benefit": ["TLT", "GLD"] if credit_relative < -1 else ["IWM", "XBI"], "pressure": ["IWM", "ARKK", "KRE"] if credit_relative < -1 else [], "discipline": "高Beta仓位减半" if credit_relative < -1 else "维持正常仓位上限"})
    if breadth_relative is not None:
        impacts.append({"driver": "等权落后" if breadth_relative < -1 else "上涨扩散", "benefit": ["SPY", "QQQ"] if breadth_relative < -1 else ["RSP", "IWM"], "pressure": ["RSP", "IWM"] if breadth_relative < -1 else [], "discipline": "警惕指数强、内部弱" if breadth_relative < -1 else "轮动参与度改善"})

    now = datetime.now(NY)
    try:
        events = fomc_events(now.date())
    except Exception as exc:
        failures["fomc_calendar"] = str(exc)
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
        events = [event for event in previous.get("events", []) if event.get("end_date", "") >= now.date().isoformat()]
    payload = {
        "version": 2, "generated_at": now.isoformat(), "timezone": "America/New_York",
        "risk": {"key": risk_key, "label": risk_label, "score": score, "headline": " · ".join(notes[:2]) or "核心数据暂缺", "equity_constraint": "暂停新增伏击" if score >= 7 else "新增伏击减半" if score >= 5 else "禁止追高、按关键位执行" if score >= 3 else "允许正常伏击与持仓"},
        "dimensions": dimensions, "fundamentals": fundamentals, "impacts": impacts[:3],
        "events": events,
        "market": market, "official": official,
        "data_quality": {"failed": len(failures), "failures": failures, "note": "免费公开源；不同序列频率不同，卡片显示各自观察日期。"},
        "sources": ["Yahoo Chart API", "U.S. Department of the Treasury", "U.S. Bureau of Labor Statistics", "Federal Reserve Bank of Atlanta GDPNow", "Federal Reserve Economic Data (FRED)", "Federal Reserve FOMC Calendar"],
    }
    if len(dimensions) < 4 or (not market and not official):
        raise RuntimeError("insufficient macro data")
    atomic_write(OUTPUT, payload)
    print(json.dumps({"risk": risk_label, "score": score, "market": len(market), "official": len(official), "failed": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
