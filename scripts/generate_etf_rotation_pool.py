#!/usr/bin/env python3
"""Mirror the youth-online ETF rotation pool and refresh quotes via stock-api.

Source boundary:
- Pool / strategy metrics come from https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f
  and its public daily-bar API.
- Realtime quote fields come from stock-api@2.7.2 CLI.
- This script does not run its own all-market ETF screening.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
RESEARCH_DIR = ROOT / "src" / "content" / "blog" / "research"
SOURCE_PAGE = "https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f"
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
    "holding_count": 2,
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Referer": SOURCE_PAGE})


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
            parsed.append({"date": str(row["date"]), "close": close, "pre_close": safe_float(row.get("pre_close"))})
    return sorted(parsed, key=lambda x: x["date"])


def fetch_stock_api_quotes(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    codes = [f"{market_prefix(x['market'])}{x['code']}" for x in items]
    cmd = ["npx", "-y", STOCK_API_PACKAGE, "get-stocks", *codes]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90, check=True)
    rows = json.loads(proc.stdout)
    return {str(x.get("code", ""))[-6:]: x for x in rows if x.get("code")}


def calc_metrics(item: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    bars = fetch_youth_daily(item)
    if len(bars) < PARAMS["ma_period"] + PARAMS["trend_window"] + 1:
        return {**item, "status": "excluded", "exclude_reason": "youth-online K线不足", "bars_count": len(bars)}
    closes = [x["close"] for x in bars]
    daily_close = closes[-1]
    price = safe_float((quote or {}).get("now"))
    if math.isfinite(price) and price > 0:
        closes[-1] = price
    else:
        price = daily_close
    ma = avg(closes[-PARAMS["ma_period"]:])
    ma_prev = avg(closes[-PARAMS["ma_period"] - PARAMS["trend_window"]:-PARAMS["trend_window"]])
    ret3 = pct(price, closes[-1 - PARAMS["short_days"]])
    ret10 = pct(price, closes[-11])
    ret20 = pct(price, closes[-1 - PARAMS["sort_period"]])
    pass_short = ret3 > PARAMS["short_threshold"]
    pass_abs = price > ma and ma > ma_prev and pass_short
    score = ret20
    stock_code = f"{market_prefix(item['market'])}{item['code']}"
    return {
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
        "high": safe_float((quote or {}).get("high")),
        "low": safe_float((quote or {}).get("low")),
        "quote_name": (quote or {}).get("name"),
        "quote_source": (quote or {}).get("source") or "stock-api",
        "ret3_ref_close": round(closes[-1 - PARAMS["short_days"]], 4),
        "ret10_ref_close": round(closes[-11], 4),
        "ret20_ref_close": round(closes[-1 - PARAMS["sort_period"]], 4),
        "ret3": round(ret3, 2),
        "ret10": round(ret10, 2),
        "ret20": round(ret20, 2),
        "ma20": round(ma, 4),
        "ma20_prev": round(ma_prev, 4),
        "score": round(score, 2),
        "checks": {
            "price_above_ma": price > ma,
            "ma_rising": ma > ma_prev,
            "short_ok": pass_short,
            "momentum": pass_abs,
        },
    }


def assign_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [dict(x) for x in rows if x.get("checks", {}).get("momentum")][: PARAMS["holding_count"]]
    for idx, row in enumerate(selected):
        row["recommended_weight"] = 50 if idx == 0 else 50
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
        return f"- 🔴 {r['name']} `{r['code']}`：20日 {r['ret20']}%，10日 {r['ret10']}%，3日 {r['ret3']}%，实时价 {r['price']}，行情源 {r.get('quote_source','stock-api')}"
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
- 实时行情：stock-api@2.7.2
- ETF池：{payload['summary']['universe_count']}只
- 动量通过：{payload['summary']['core_count']}只
- Top 2：{', '.join([f"{x['name']} {x['code']}" for x in rec]) or '空仓/货币ETF'}

## Top 候选

{chr(10).join(row_line(r) for r in rows) if rows else '- 当前无动量通过候选。'}

## 执行口径

轮动池直接镜像 youth-online 原网页固定池与双动量参数；本站负责展示、排序、移动端阅读体验，并用 stock-api 刷新当前价和涨跌幅。
"""
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    start = now_cn()
    quotes = fetch_stock_api_quotes(SOURCE_POOL)
    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(calc_metrics, item, quotes.get(item["code"])) for item in SOURCE_POOL]
        for fut in concurrent.futures.as_completed(futures):
            rows.append(fut.result())
            time.sleep(0.05)
    rows.sort(key=lambda r: r.get("score", -999), reverse=True)
    core = [x for x in rows if x.get("status") == "core"]
    watch = [x for x in rows if x.get("status") == "watch"]
    excluded = [x for x in rows if x.get("status") == "excluded"]
    recommendations = assign_recommendations(core)
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S %Z")
    run_date = now_cn().date().isoformat()
    latest_trade_date = max((x.get("date", "") for x in rows), default="")
    payload = {
        "generated_at": generated_at,
        "run_date": run_date,
        "evaluation_date": local_today(),
        "latest_trade_date": latest_trade_date,
        "source_page": SOURCE_PAGE,
        "pool_source": "youth-online-source-page",
        "quote_source": "stock-api@2.7.2",
        "params": PARAMS,
        "summary": {
            "universe_source": "youth-online-source-page",
            "universe_count": len(SOURCE_POOL),
            "valid_count": len([x for x in rows if "price" in x]),
            "core_count": len(core),
            "watch_count": len(watch),
            "excluded_count": len(excluded),
            "momentum_pass_count": len(core),
        },
        "recommendations": recommendations,
        "core_pool": core,
        "watch_pool": watch,
        "excluded_sample": excluded,
        "all_rows": rows,
    }
    write_json(PUBLIC_DATA / "etf-momentum-latest.json", payload)
    write_json(PUBLIC_DATA / "etf-rotation-pool.json", {"generated_at": generated_at, "source_page": SOURCE_PAGE, "items": core})
    write_json(PUBLIC_DATA / "etf-screening-report.json", payload)
    blog_path = write_blog(payload)
    elapsed = (now_cn() - start).total_seconds()
    print(f"ETF轮动池镜像完成：youth-online池{len(SOURCE_POOL)}只，动量通过{len(core)}只，stock-api实时行情已更新，耗时{elapsed:.0f}s，博客 {blog_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
