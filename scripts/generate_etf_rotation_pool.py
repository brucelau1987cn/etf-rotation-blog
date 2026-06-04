#!/usr/bin/env python3
"""Mirror the youth-online ETF rotation pool and refresh K-lines/quotes via stock-api.

Source boundary:
- Pool / strategy metrics come from https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f.
- Daily bars and realtime quote fields come from stock-api@2.7.2 CLI first.
- youth-online daily-bar API is retained as a K-line fallback.
- This script does not run its own all-market ETF screening.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
RESEARCH_DIR = ROOT / "src" / "content" / "blog" / "research"
SOURCE_PAGE = "https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f"
YOUTH_ORIGIN = "https://etf.youth-online.site"
YOUTH_POOL_PAGES = [SOURCE_PAGE, f"{YOUTH_ORIGIN}/"]
YOUTH_DAILY = "https://etf.youth-online.site/api/etf/{market}/daily"
STOCK_API_PACKAGE = "stock-api@2.7.2"

SOURCE_POOL = [
    {"name": "纳指 ETF", "code": "159501", "market": "XSHE", "type": "海外"},
    {"name": "芯片 ETF", "code": "512760", "market": "XSHG", "type": "行业"},
    {"name": "创业板 ETF", "code": "159915", "market": "XSHE", "type": "宽基"},
    {"name": "人工智能 ETF", "code": "159819", "market": "XSHE", "type": "行业"},
    {"name": "机器人 ETF", "code": "562500", "market": "XSHG", "type": "行业"},
    {"name": "标普 500ETF", "code": "513500", "market": "XSHG", "type": "海外"},
    {"name": "科创 50ETF", "code": "588000", "market": "XSHG", "type": "宽基"},
    {"name": "恒生科技 ETF", "code": "513180", "market": "XSHG", "type": "海外"},
    {"name": "沪深 300ETF", "code": "510300", "market": "XSHG", "type": "宽基"},
    {"name": "德国 ETF", "code": "513030", "market": "XSHG", "type": "海外"},
    {"name": "中证 A500ETF", "code": "159339", "market": "XSHE", "type": "宽基"},
    {"name": "银华日利 ETF", "code": "511880", "market": "XSHG", "type": "货币"},
    {"name": "华宝添益 ETF", "code": "511990", "market": "XSHG", "type": "货币"},
    {"name": "中证 500ETF", "code": "510500", "market": "XSHG", "type": "宽基"},
    {"name": "红利低波 ETF", "code": "512890", "market": "XSHG", "type": "行业"},
    {"name": "黄金 ETF", "code": "518880", "market": "XSHG", "type": "商品"},
    {"name": "证券 ETF", "code": "512880", "market": "XSHG", "type": "行业"},
    {"name": "有色金属 ETF", "code": "512400", "market": "XSHG", "type": "行业"},
    {"name": "新能源 ETF", "code": "159875", "market": "XSHE", "type": "行业"},
    {"name": "消费 ETF", "code": "159928", "market": "XSHE", "type": "行业"},
    {"name": "医药 ETF", "code": "512010", "market": "XSHG", "type": "行业"},
    {"name": "军工 ETF", "code": "512660", "market": "XSHG", "type": "行业"},
]

PARAMS = {
    "ma_period": 20,
    "trend_window": 5,
    "short_days": 3,
    "short_threshold": -5.0,
    "sort_period": 20,
    "holding_count": 3,
    "slope_short_window": 20,
    "slope_long_window": 60,
}

BENCHMARK_BY_TYPE = {
    "海外": "510300",
    "宽基": "510300",
    "行业": "510300",
    "商品": "510300",
    "货币": "511880",
}

DEFENSIVE_ASSETS = [
    {"name": "银华日利 ETF", "code": "511880", "role": "现金替代"},
    {"name": "华宝添益 ETF", "code": "511990", "role": "现金替代"},
    {"name": "黄金 ETF", "code": "518880", "role": "避险资产"},
    {"name": "红利低波 ETF", "code": "512890", "role": "权益防御"},
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Referer": SOURCE_PAGE})


def normalize_source_pool_item(item: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(item.get("name", "")).strip(),
        "code": str(item.get("code", "")).strip(),
        "market": str(item.get("exchange_code") or item.get("market") or "").strip(),
        "type": str(item.get("asset_type") or item.get("type") or "").strip(),
    }


def parse_js_object_array(raw: str) -> list[dict[str, str]]:
    normalized = re.sub(r"([,{])([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', raw)
    normalized = normalized.replace("`", '"').replace("'", '"')
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    return json.loads(normalized)


def extract_pool_from_page(page_url: str) -> list[dict[str, str]]:
    page = SESSION.get(page_url, timeout=20)
    page.raise_for_status()
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)', page.text)
    for src in scripts:
        js_url = urljoin(YOUTH_ORIGIN, src)
        js = SESSION.get(js_url, timeout=20).text
        match = re.search(r"ETF_POOL:\s*(\[\{.*?\}\])\s*,\s*etfData", js)
        if not match:
            continue
        items = [normalize_source_pool_item(x) for x in parse_js_object_array(match.group(1))]
        valid = [x for x in items if x["name"] and x["code"] and x["market"] in {"XSHG", "XSHE"} and x["type"]]
        if valid:
            return valid
    return []


def fetch_youth_source_pool() -> tuple[list[dict[str, str]], str]:
    errors: list[str] = []
    for page_url in YOUTH_POOL_PAGES:
        try:
            pool = extract_pool_from_page(page_url)
            if pool:
                return pool, f"youth-online-page-js-etf-pool:{page_url}"
            errors.append(f"{page_url}: 未提取到 ETF_POOL")
        except Exception as exc:
            errors.append(f"{page_url}: {exc}")
    if SOURCE_POOL:
        return SOURCE_POOL, "local-source-pool-fallback"
    raise RuntimeError("未能同步 youth-online ETF_POOL；" + "；".join(errors))


def load_source_pool() -> tuple[list[dict[str, str]], str]:
    return fetch_youth_source_pool()


def now_cn() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def market_prefix(market: str) -> str:
    return "SH" if market == "XSHG" else "SZ"


def local_today() -> str:
    return now_cn().date().isoformat()


def start_date() -> str:
    return (now_cn().date() - dt.timedelta(days=80)).isoformat()


def safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return math.nan


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b and math.isfinite(a) and math.isfinite(b) else math.nan


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if not math.isfinite(value):
        return low
    return max(low, min(high, value))


def calc_slope_momentum(closes: list[float]) -> float:
    valid = [x for x in closes if math.isfinite(x) and x > 0]
    if len(valid) < 3:
        return math.nan
    logs = [math.log(x) for x in valid]
    n = len(logs)
    xs = list(range(1, n + 1))
    mean_x = avg([float(x) for x in xs])
    mean_y = avg(logs)
    numerator = sum((x - mean_x) * (logs[i] - mean_y) for i, x in enumerate(xs))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return math.nan
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    fitted = [intercept + slope * x for x in xs]
    ss_res = sum((logs[i] - fitted[i]) ** 2 for i in range(n))
    ss_tot = sum((y - mean_y) ** 2 for y in logs)
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1 - ss_res / ss_tot)
    annualized_return = math.exp(slope * 250) - 1
    return annualized_return * r2


def score_signal(row: dict[str, Any]) -> float:
    checks = row.get("checks", {})
    ret5 = safe_float(row.get("ret5"))
    slope20 = safe_float(row.get("slope20_score"))
    slope60 = safe_float(row.get("slope60_score"))
    volume_ratio = safe_float(row.get("volume_ratio"))
    close_position = safe_float(row.get("close_position"))
    relative_strength = safe_float(row.get("relative_strength"))
    chip_ice = safe_float(row.get("chip_ice_score"))
    risk_penalty = safe_float(row.get("risk_penalty"))

    short_score = clamp((ret5 + 5) / 12 * 100)
    slope20_score = clamp((slope20 + 0.2) / 1.6 * 100)
    trend_score = 0.0
    trend_score += 35 if checks.get("price_above_ma") else 0
    trend_score += 35 if checks.get("ma20_above_ma60") else 0
    trend_score += 20 if slope60 > 0 else 0
    trend_score += 10 if relative_strength > 0 else 0
    flow_score = clamp((volume_ratio - 0.7) / 1.0 * 100)
    position_score = clamp(close_position * 100)
    chip_score = clamp(chip_ice)
    risk_score = clamp(100 - risk_penalty * 8)

    return round(
        short_score * 0.20
        + slope20_score * 0.20
        + trend_score * 0.15
        + flow_score * 0.15
        + position_score * 0.10
        + chip_score * 0.10
        + risk_score * 0.10,
        2,
    )


def decide_action(row: dict[str, Any]) -> str:
    score = safe_float(row.get("signal_score"))
    rank = int(row.get("momentum_rank") or 999)
    risks = row.get("risk_flags") or []
    if score < 50 or rank > 15 or "跌破20日线" in risks:
        return "退出"
    if score < 60 or rank > 10 or any(x in risks for x in ["高溢价", "放量长上影"]):
        return "减仓"
    if score >= 80 and rank <= 3 and row.get("status") == "core":
        return "加仓"
    if score >= 65 and rank <= 5:
        return "持有"
    return "观察"


def detect_market_regime(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if math.isfinite(safe_float(r.get("signal_score")))]
    strong_count = len([r for r in valid if safe_float(r.get("signal_score")) >= 70])
    top5 = sorted(valid, key=lambda r: safe_float(r.get("signal_score")), reverse=True)[:5]
    top5_avg = avg([safe_float(r.get("signal_score")) for r in top5]) if top5 else 0.0
    if strong_count >= 8 and top5_avg >= 75:
        state, equity, defense = "进攻", "50%-60%", "10%-20%"
    elif strong_count >= 5 and top5_avg >= 65:
        state, equity, defense = "震荡", "30%-50%", "30%-40%"
    elif strong_count >= 2:
        state, equity, defense = "防御", "10%-30%", "50%-70%"
    else:
        state, equity, defense = "极弱", "0%-10%", "80%-100%"
    return {
        "state": state,
        "strong_count": strong_count,
        "top5_avg_score": round(top5_avg, 2),
        "equity_allocation": equity,
        "defense_allocation": defense,
        "defensive_assets": DEFENSIVE_ASSETS,
    }


def parse_stock_api_klines(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in raw_rows:
        close = safe_float(row.get("close"))
        date = row.get("date")
        if not date or not math.isfinite(close):
            continue
        parsed.append(
            {
                "date": str(date),
                "open": safe_float(row.get("open")),
                "high": safe_float(row.get("high")),
                "low": safe_float(row.get("low")),
                "close": close,
                "volume": safe_float(row.get("volume")),
                "source": row.get("source") or "stock-api",
            }
        )
    return sorted(parsed, key=lambda x: x["date"])


def fetch_stock_api_klines(item: dict[str, Any], count: int = 90) -> list[dict[str, Any]]:
    stock_code = f"{market_prefix(item['market'])}{item['code']}"
    cmd = [
        "npx",
        "-y",
        STOCK_API_PACKAGE,
        "get-klines",
        stock_code,
        "--period",
        "day",
        "--count",
        str(count),
        "--adjust",
        "none",
        "--source",
        "auto",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90, check=True)
    return parse_stock_api_klines(json.loads(proc.stdout))


def fetch_youth_daily(item: dict[str, Any]) -> list[dict[str, Any]]:
    params = {
        "ticker": item["code"],
        "start_date": start_date(),
        "end_date": local_today(),
        "columns": "date,close,pre_close",
    }
    url = YOUTH_DAILY.format(market=item["market"])
    r = SESSION.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    rows = data.get("data") or []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        close = safe_float(row.get("close"))
        if row.get("date") and math.isfinite(close):
            parsed.append(
                {
                    "date": str(row["date"]),
                    "close": close,
                    "pre_close": safe_float(row.get("pre_close")),
                    "source": "youth-online",
                }
            )
    return sorted(parsed, key=lambda x: x["date"])


def fetch_daily_bars(item: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    errors: list[str] = []
    try:
        bars = fetch_stock_api_klines(item)
        if bars:
            source = bars[-1].get("source") or "stock-api"
            return bars, f"stock-api@2.7.2:{source}"
        errors.append("stock-api K线为空")
    except Exception as exc:
        errors.append(f"stock-api K线失败：{exc}")
    try:
        bars = fetch_youth_daily(item)
        if bars:
            return bars, "youth-online-daily-fallback"
        errors.append("youth-online K线为空")
    except Exception as exc:
        errors.append(f"youth-online K线失败：{exc}")
    raise RuntimeError("；".join(errors))


def fetch_stock_api_quotes(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    codes = [f"{market_prefix(x['market'])}{x['code']}" for x in items]
    cmd = ["npx", "-y", STOCK_API_PACKAGE, "get-stocks", *codes]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90, check=True)
    rows = json.loads(proc.stdout)
    return {str(x.get("code", ""))[-6:]: x for x in rows if x.get("code")}


def calc_metrics(item: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        bars, kline_source = fetch_daily_bars(item)
    except Exception as exc:
        return {**item, "status": "excluded", "exclude_reason": f"K线获取失败：{exc}", "bars_count": 0}
    required = PARAMS["ma_period"] + PARAMS["trend_window"] + 1
    if len(bars) < required:
        return {**item, "status": "excluded", "exclude_reason": "K线不足", "bars_count": len(bars), "kline_source": kline_source}
    closes = [x["close"] for x in bars]
    daily_close = closes[-1]
    price = safe_float((quote or {}).get("now"))
    if math.isfinite(price) and price > 0:
        closes[-1] = price
    else:
        price = daily_close
    ma = avg(closes[-PARAMS["ma_period"]:])
    ma_prev = avg(closes[-PARAMS["ma_period"] - PARAMS["trend_window"]:-PARAMS["trend_window"]])
    ma60 = avg(closes[-60:])
    ret3 = pct(price, closes[-1 - PARAMS["short_days"]])
    ret5 = pct(price, closes[-6])
    ret10 = pct(price, closes[-11])
    ret20 = pct(price, closes[-1 - PARAMS["sort_period"]])
    slope20 = calc_slope_momentum(closes[-PARAMS["slope_short_window"]:])
    slope60 = calc_slope_momentum(closes[-PARAMS["slope_long_window"]:])
    pass_short = ret3 > PARAMS["short_threshold"]
    above_ma20 = price > ma
    ma_rising = ma > ma_prev
    ma20_above_ma60 = ma > ma60
    pass_dual = ret5 > 0 and slope20 > 0 and above_ma20
    pass_abs = above_ma20 and ma_rising and pass_short and pass_dual
    recent_volumes = [safe_float(x.get("volume")) for x in bars[-5:-1]]
    recent_volumes = [x for x in recent_volumes if math.isfinite(x) and x > 0]
    latest_volume = safe_float(bars[-1].get("volume"))
    volume_ratio = latest_volume / avg(recent_volumes) if recent_volumes and math.isfinite(latest_volume) and latest_volume > 0 else 1.0
    quote_high = safe_float((quote or {}).get("high"))
    quote_low = safe_float((quote or {}).get("low"))
    close_position = 0.5
    if math.isfinite(quote_high) and math.isfinite(quote_low) and quote_high > quote_low:
        close_position = (price - quote_low) / (quote_high - quote_low)
    benchmark_code = BENCHMARK_BY_TYPE.get(item.get("type"), "510300")
    benchmark_momentum = 0.0
    relative_strength = ret20 - benchmark_momentum if math.isfinite(ret20) else math.nan
    risk_flags: list[str] = []
    if not above_ma20:
        risk_flags.append("跌破20日线")
    if close_position < 0.4:
        risk_flags.append("收盘偏弱")
    if ret5 > 10:
        risk_flags.append("短线过热")
    risk_penalty = len(risk_flags)
    chip_ice_score = 50.0
    stock_code = f"{market_prefix(item['market'])}{item['code']}"
    row = {
        **item,
        "market": "sh" if item["market"] == "XSHG" else "sz",
        "source_market": item["market"],
        "stock_code": stock_code,
        "status": "core" if pass_abs else "watch",
        "date": bars[-1]["date"],
        "evaluation_date": local_today(),
        "price": round(price, 4),
        "daily_close": round(daily_close, 4),
        "prev_close": safe_float((quote or {}).get("yesterday")),
        "change_pct": round(safe_float((quote or {}).get("percent")) * 100, 2) if quote else None,
        "high": quote_high,
        "low": quote_low,
        "quote_name": (quote or {}).get("name"),
        "quote_source": (quote or {}).get("source") or "stock-api",
        "kline_source": kline_source,
        "bars_count": len(bars),
        "ret3_ref_close": round(closes[-1 - PARAMS["short_days"]], 4),
        "ret5_ref_close": round(closes[-6], 4),
        "ret10_ref_close": round(closes[-11], 4),
        "ret20_ref_close": round(closes[-1 - PARAMS["sort_period"]], 4),
        "ret3": round(ret3, 2),
        "ret5": round(ret5, 2),
        "ret10": round(ret10, 2),
        "ret20": round(ret20, 2),
        "ma20": round(ma, 4),
        "ma20_prev": round(ma_prev, 4),
        "ma60": round(ma60, 4),
        "slope20_score": round(slope20, 4),
        "slope60_score": round(slope60, 4),
        "volume_ratio": round(volume_ratio, 2),
        "close_position": round(clamp(close_position, 0, 1), 2),
        "benchmark_code": benchmark_code,
        "benchmark_momentum": round(benchmark_momentum, 2),
        "relative_strength": round(relative_strength, 2),
        "chip_ice_score": chip_ice_score,
        "risk_flags": risk_flags,
        "risk_penalty": risk_penalty,
        "checks": {
            "price_above_ma": above_ma20,
            "ma_rising": ma_rising,
            "ma20_above_ma60": ma20_above_ma60,
            "short_ok": pass_short,
            "dual_momentum": pass_dual,
            "relative_strength": relative_strength > 0,
            "momentum": pass_abs,
        },
    }
    row["signal_score"] = score_signal(row)
    row["score"] = row["signal_score"]
    return row


def assign_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        dict(x)
        for x in rows
        if x.get("checks", {}).get("momentum") and safe_float(x.get("signal_score")) >= 60
    ][: PARAMS["holding_count"]]
    weights = [20, 15, 15]
    for idx, row in enumerate(selected):
        row["recommended_weight"] = weights[idx] if idx < len(weights) else 10
        row["action"] = decide_action(row)
    return selected


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_blog(payload: dict[str, Any]) -> Path:
    run_date = payload["run_date"]
    rows = payload["core_pool"][:10]
    rec = payload["recommendations"]
    path = RESEARCH_DIR / f"{run_date}-etf-rotation-pool.md"
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    def row_line(r: dict[str, Any]) -> str:
        return f"- 🔴 {r['name']} `{r['code']}`：得分 {r.get('signal_score','—')}，动作 {r.get('action','观察')}，20日 {r['ret20']}%，5日 {r.get('ret5','—')}%，斜率R² {r.get('slope20_score','—')}，实时价 {r['price']}，行情源 {r.get('quote_source','stock-api')}"
    content = f"""---
title: 'ETF轮动池镜像：{run_date}'
description: '轮动池取自 youth-online 原网页，实时行情由 stock-api 更新。'
pubDate: {run_date}
category: '研测'
---

## 结论

- 评估日期：{payload['evaluation_date']}
- 行情日期：{payload['latest_trade_date']}
- 轮动池来源：{SOURCE_PAGE}
- ETF名单同步：{payload.get('pool_source', 'youth-online')}
- 实时行情：stock-api v2.7.2
- K线数据：stock-api v2.7.2 优先，youth-online 备用
- ETF池：{payload['summary']['universe_count']}只
- 动量通过：{payload['summary']['core_count']}只
- Top 3：{', '.join([f"{x['name']} {x['code']}({x.get('action','观察')})" for x in rec]) or '空仓/货币ETF'}
- 市场状态：{payload.get('market_regime', {}).get('state', '—')}，权益仓 {payload.get('market_regime', {}).get('equity_allocation', '—')}，防御仓 {payload.get('market_regime', {}).get('defense_allocation', '—')}

## Top 候选

{chr(10).join(row_line(r) for r in rows) if rows else '- 当前无动量通过候选。'}

## 执行口径

轮动池 ETF 名单从 youth-online 页面脚本同步，双动量参数跟随源页；本站负责展示、排序、移动端阅读体验，并用 stock-api 刷新当前价和涨跌幅。
"""
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    start = now_cn()
    source_pool, pool_source = load_source_pool()
    quotes = fetch_stock_api_quotes(source_pool)
    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(calc_metrics, item, quotes.get(item["code"])) for item in source_pool]
        for fut in concurrent.futures.as_completed(futures):
            rows.append(fut.result())
            time.sleep(0.05)
    rows.sort(key=lambda r: r.get("score", -999), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["momentum_rank"] = idx
        row["action"] = decide_action(row)
    core = [x for x in rows if x.get("status") == "core"]
    watch = [x for x in rows if x.get("status") == "watch"]
    excluded = [x for x in rows if x.get("status") == "excluded"]
    recommendations = assign_recommendations(core)
    market_regime = detect_market_regime(rows)
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S %Z")
    run_date = now_cn().date().isoformat()
    latest_trade_date = max((x.get("date", "") for x in rows), default="")
    payload = {
        "generated_at": generated_at,
        "run_date": run_date,
        "evaluation_date": local_today(),
        "latest_trade_date": latest_trade_date,
        "source_page": SOURCE_PAGE,
        "pool_source": pool_source,
        "quote_source": "stock-api@2.7.2",
        "kline_source": "stock-api@2.7.2 primary; youth-online fallback",
        "params": PARAMS,
        "summary": {
            "universe_source": pool_source,
            "universe_count": len(source_pool),
            "valid_count": len([x for x in rows if "price" in x]),
            "core_count": len(core),
            "watch_count": len(watch),
            "excluded_count": len(excluded),
            "momentum_pass_count": len(core),
        },
        "market_regime": market_regime,
        "defensive_assets": DEFENSIVE_ASSETS,
        "recommendations": recommendations,
        "core_pool": core,
        "watch_pool": watch,
        "excluded_sample": excluded,
        "all_rows": rows,
    }
    write_json(PUBLIC_DATA / "etf-momentum-latest.json", payload)
    write_json(PUBLIC_DATA / "etf-rotation-pool.json", {"generated_at": generated_at, "source_page": SOURCE_PAGE, "kline_source": payload["kline_source"], "items": core})
    write_json(PUBLIC_DATA / "etf-screening-report.json", payload)
    blog_path = write_blog(payload)
    elapsed = (now_cn() - start).total_seconds()
    print(f"ETF轮动池镜像完成：{pool_source} {len(source_pool)}只，动量通过{len(core)}只，stock-api K线/实时行情已更新，耗时{elapsed:.0f}s，博客 {blog_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
