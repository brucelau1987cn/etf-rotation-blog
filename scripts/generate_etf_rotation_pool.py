#!/usr/bin/env python3
"""Generate ETF rotation-pool data and a daily research snapshot.

Data route:
- Universe: Eastmoney ETF list endpoint, with a conservative built-in core ETF fallback.
- Daily bars: Tencent kline endpoint.
- Realtime quote: Tencent quote endpoint.
- Premium proxy: Eastmoney fund valuation endpoint (fundgz.1234567.com.cn).

The script is designed for a nightly 20:00 run. It writes static JSON files under
public/data/ and a research blog snapshot under src/content/blog/research/.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
RESEARCH_DIR = ROOT / "src" / "content" / "blog" / "research"

EASTMONEY_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FUND_SEARCH = "https://fund.eastmoney.com/js/fundcode_search.js"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
TENCENT_QUOTE = "https://qt.gtimg.cn/q={symbol}"
FUND_GZ = "https://fundgz.1234567.com.cn/js/{code}.js"

PARAMS = {
    "ma_period": 20,
    "trend_window": 5,
    "short_days": 3,
    "short_threshold": -5.0,
    "sort_period": 20,
    "holding_count": 2,
    "min_avg_amount": 30_000_000,
    "premium_limit": 1.5,
    "overheat_ret20": 25.0,
    "overheat_ret3": 8.0,
    "max_drawdown_20d": 12.0,
    "min_volume_heat": 0.8,
    "cross_border_premium_watch": 1.0,
}

CORE_FALLBACK = [
    {"name": "纳指ETF", "code": "159501", "market": "sz", "type": "海外"},
    {"name": "芯片ETF", "code": "512760", "market": "sh", "type": "行业"},
    {"name": "创业板ETF", "code": "159915", "market": "sz", "type": "宽基"},
    {"name": "人工智能ETF", "code": "159819", "market": "sz", "type": "行业"},
    {"name": "机器人ETF", "code": "562500", "market": "sh", "type": "行业"},
    {"name": "标普500ETF", "code": "513500", "market": "sh", "type": "海外"},
    {"name": "科创50ETF", "code": "588000", "market": "sh", "type": "宽基"},
    {"name": "恒生科技ETF", "code": "513180", "market": "sh", "type": "海外"},
    {"name": "沪深300ETF", "code": "510300", "market": "sh", "type": "宽基"},
    {"name": "德国ETF", "code": "513030", "market": "sh", "type": "海外"},
    {"name": "中证A500ETF", "code": "159339", "market": "sz", "type": "宽基"},
    {"name": "银华日利ETF", "code": "511880", "market": "sh", "type": "货币"},
    {"name": "华宝添益ETF", "code": "511990", "market": "sh", "type": "货币"},
    {"name": "中证500ETF", "code": "510500", "market": "sh", "type": "宽基"},
    {"name": "红利低波ETF", "code": "512890", "market": "sh", "type": "行业"},
    {"name": "黄金ETF", "code": "518880", "market": "sh", "type": "商品"},
    {"name": "证券ETF", "code": "512880", "market": "sh", "type": "行业"},
    {"name": "有色金属ETF", "code": "512400", "market": "sh", "type": "行业"},
    {"name": "新能源ETF", "code": "159875", "market": "sz", "type": "行业"},
    {"name": "消费ETF", "code": "159928", "market": "sz", "type": "行业"},
    {"name": "医药ETF", "code": "512010", "market": "sh", "type": "行业"},
    {"name": "军工ETF", "code": "512660", "market": "sh", "type": "行业"},
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
})


def now_cn() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def request_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = 15, tries: int = 3) -> Any:
    last: Exception | None = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"request_json failed: {url} {last}")


def classify_type(name: str) -> str:
    n = name.replace(" ", "")
    if any(k in n for k in ["纳指", "标普", "恒生", "日经", "德国", "法国", "港股", "中概", "海外", "QDII", "东南亚"]):
        return "海外"
    if any(k in n for k in ["货币", "添益", "日利", "保证金"]):
        return "货币"
    if any(k in n for k in ["黄金", "豆粕", "有色", "能源化工", "商品", "油气"]):
        return "商品"
    if any(k in n for k in ["沪深", "中证", "科创", "创业板", "上证", "深证", "A500", "500", "300", "50", "1000", "2000"]):
        return "宽基"
    return "行业"


def market_from_code(code: str, f13: Any = None) -> str:
    if str(f13) == "1" or code.startswith(("5", "6")):
        return "sh"
    return "sz"


def fetch_universe() -> tuple[list[dict[str, Any]], str, int | None]:
    items: list[dict[str, Any]] = []
    total: int | None = None
    page_size = 50
    for pn in range(1, 80):
        params = {
            "pn": pn,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": "f12,f14,f13,f3",
        }
        try:
            data = request_json(EASTMONEY_LIST, params=params, timeout=12, tries=3).get("data") or {}
            total = data.get("total") or total
            diff = data.get("diff") or []
            if not diff:
                break
            for row in diff:
                code = str(row.get("f12") or "").strip()
                name = str(row.get("f14") or "").strip()
                if re.fullmatch(r"\d{6}", code) and "ETF" in name.upper():
                    items.append({
                        "name": name,
                        "code": code,
                        "market": market_from_code(code, row.get("f13")),
                        "type": classify_type(name),
                    })
            if total and len(items) >= total:
                break
            time.sleep(0.2)
        except Exception:
            if pn == 1 and not items:
                break
            continue
    if len(items) >= 200:
        dedup = {x["code"]: x for x in items}
        return list(dedup.values()), "eastmoney", total
    fund_items = fetch_universe_from_fund_search()
    if len(fund_items) >= 200:
        dedup = {x["code"]: x for x in fund_items}
        return list(dedup.values()), "eastmoney_fund_search", len(fund_items)
    return CORE_FALLBACK, "core_fallback", len(CORE_FALLBACK)


def fetch_universe_from_fund_search() -> list[dict[str, Any]]:
    try:
        text = SESSION.get(EASTMONEY_FUND_SEARCH, timeout=25, headers={"Referer": "https://fund.eastmoney.com/"}).text
        m = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?", text, re.S)
        if not m:
            return []
        rows = json.loads(m.group(1))
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            code = str(row[0]).strip()
            name = str(row[2]).strip()
            fund_type = str(row[3]).strip()
            upper_name = name.upper()
            if not re.fullmatch(r"\d{6}", code):
                continue
            if "ETF" not in upper_name:
                continue
            if any(k in name for k in ["联接", "连接", "发起式", "场外", "C类", "A类"]):
                continue
            if "ETF-场内" not in fund_type and "ETF" not in fund_type and not code.startswith(("15", "51", "56", "58")):
                continue
            items.append({
                "name": name,
                "code": code,
                "market": market_from_code(code),
                "type": classify_type(name),
            })
        return items
    except Exception:
        return []


def fetch_kline(item: dict[str, Any], count: int = 65) -> list[dict[str, Any]]:
    symbol = f"{item['market']}{item['code']}"
    params = {"param": f"{symbol},day,,,{count}"}
    data = request_json(TENCENT_KLINE, params=params, timeout=12, tries=3)
    raw = ((data.get("data") or {}).get(symbol) or {}).get("day") or []
    rows = []
    for x in raw:
        try:
            rows.append({
                "date": x[0],
                "open": float(x[1]),
                "close": float(x[2]),
                "high": float(x[3]),
                "low": float(x[4]),
                "volume": float(x[5]),
            })
        except Exception:
            continue
    return rows


def fetch_quote(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = f"{item['market']}{item['code']}"
    try:
        text = SESSION.get(TENCENT_QUOTE.format(symbol=symbol), timeout=8).text
        body = text.split('="', 1)[1].rsplit('"', 1)[0]
        parts = body.split("~")
        if len(parts) < 38:
            return None
        return {
            "name": parts[1],
            "price": float(parts[3]),
            "prev_close": float(parts[4]),
            "open": float(parts[5]),
            "volume_hands": float(parts[6]),
            "time": parts[30],
            "change": float(parts[31]),
            "change_pct": float(parts[32]),
            "high": float(parts[33]),
            "low": float(parts[34]),
            "amount": float(parts[37]) * 10000,
        }
    except Exception:
        return None


def fetch_premium(item: dict[str, Any], price: float) -> dict[str, Any]:
    try:
        text = SESSION.get(FUND_GZ.format(code=item["code"]), timeout=8, headers={"Referer": "https://fund.eastmoney.com/"}).text
        m = re.search(r"jsonpgz\((.*)\);?", text)
        if not m:
            return {"available": False, "reason": "fundgz_empty"}
        data = json.loads(m.group(1))
        est_nav = float(data.get("gsz") or 0)
        if est_nav <= 0:
            return {"available": False, "reason": "no_est_nav"}
        premium = (price / est_nav - 1) * 100
        return {
            "available": True,
            "est_nav": est_nav,
            "premium": premium,
            "gztime": data.get("gztime"),
            "nav_date": data.get("jzrq"),
        }
    except Exception:
        return {"available": False, "reason": "premium_fetch_failed"}


def avg(values: list[float]) -> float:
    return sum(values) / len(values)


def safe_pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else math.nan


def calc_one(item: dict[str, Any]) -> dict[str, Any]:
    try:
        bars = fetch_kline(item)
        if len(bars) < PARAMS["ma_period"] + PARAMS["trend_window"] + 1:
            return {**item, "status": "excluded", "exclude_reason": "数据不足", "bars_count": len(bars)}
        quote = fetch_quote(item)
        closes = [x["close"] for x in bars]
        last = dict(bars[-1])
        if quote and quote.get("price"):
            last["close"] = quote["price"]
            closes[-1] = quote["price"]
        price = closes[-1]
        ma_n = PARAMS["ma_period"]
        trend_window = PARAMS["trend_window"]
        ma = avg(closes[-ma_n:])
        ma_prev = avg(closes[-ma_n - trend_window:-trend_window])
        ret3 = safe_pct(price, closes[-1 - PARAMS["short_days"]])
        ret10 = safe_pct(price, closes[-11])
        ret20 = safe_pct(price, closes[-1 - PARAMS["sort_period"]])
        high20 = max(x["high"] for x in bars[-20:])
        drawdown20 = (price / high20 - 1) * 100
        amount20 = avg([x["volume"] * 100 * x["close"] for x in bars[-20:]])
        amount3 = avg([x["volume"] * 100 * x["close"] for x in bars[-3:]])
        volume_heat = amount3 / amount20 if amount20 else 0
        premium = fetch_premium(item, price)

        pass_liquidity = amount20 >= PARAMS["min_avg_amount"]
        pass_momentum = price > ma and ma > ma_prev and ret3 > PARAMS["short_threshold"]
        pass_overheat = ret20 <= PARAMS["overheat_ret20"] and ret3 <= PARAMS["overheat_ret3"]
        pass_drawdown = drawdown20 >= -PARAMS["max_drawdown_20d"]
        pass_volume_heat = volume_heat >= PARAMS["min_volume_heat"]
        if premium.get("available"):
            premium_abs = abs(float(premium["premium"]))
            pass_premium = premium_abs <= PARAMS["premium_limit"]
        else:
            premium_abs = None
            pass_premium = item.get("type") == "货币"

        hard_pass = pass_liquidity and pass_momentum and pass_premium
        risk_pass = pass_overheat and pass_drawdown and pass_volume_heat
        score = (
            ret20 * 0.40
            + ret10 * 0.20
            + ((ma / ma_prev - 1) * 100) * 0.15
            + min(volume_heat, 2.0) * 10 * 0.15
            + (max(0.0, PARAMS["premium_limit"] - (premium_abs or 0)) / PARAMS["premium_limit"] * 10) * 0.10
        )
        status = "core" if hard_pass and risk_pass else "watch" if hard_pass else "excluded"
        reasons = []
        checks = {
            "liquidity": pass_liquidity,
            "momentum": pass_momentum,
            "premium": pass_premium,
            "overheat": pass_overheat,
            "drawdown": pass_drawdown,
            "volume_heat": pass_volume_heat,
        }
        labels = {
            "liquidity": "流动性不足",
            "momentum": "动量未过",
            "premium": "折溢价超限或缺失",
            "overheat": "短线过热",
            "drawdown": "20日回撤过大",
            "volume_heat": "近3日量能不足",
        }
        for k, ok in checks.items():
            if not ok:
                reasons.append(labels[k])
        return {
            **item,
            "status": status,
            "date": last["date"],
            "price": round(price, 4),
            "change_pct": quote.get("change_pct") if quote else None,
            "quote_time": quote.get("time") if quote else None,
            "ret3_ref_close": round(closes[-1 - PARAMS["short_days"]], 4),
            "ret10_ref_close": round(closes[-11], 4),
            "ret20_ref_close": round(closes[-1 - PARAMS["sort_period"]], 4),
            "ret3": round(ret3, 2),
            "ret10": round(ret10, 2),
            "ret20": round(ret20, 2),
            "ma20": round(ma, 4),
            "ma20_prev": round(ma_prev, 4),
            "ma20_slope": round((ma / ma_prev - 1) * 100, 2),
            "drawdown20": round(drawdown20, 2),
            "avg_amount20": round(amount20, 0),
            "volume_heat": round(volume_heat, 2),
            "premium": premium,
            "score": round(score, 2),
            "checks": checks,
            "exclude_reason": "、".join(reasons),
        }
    except Exception as exc:  # noqa: BLE001
        return {**item, "status": "excluded", "exclude_reason": f"抓取失败: {type(exc).__name__}"}


def assign_recommendations(core: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_types: set[str] = set()
    for row in core:
        if len(selected) >= PARAMS["holding_count"]:
            break
        # Reduce theme concentration: prefer different high-level asset types first.
        if row["type"] in used_types and len(core) > PARAMS["holding_count"]:
            continue
        selected.append(row)
        used_types.add(row["type"])
    for row in core:
        if len(selected) >= PARAMS["holding_count"]:
            break
        if row["code"] not in {x["code"] for x in selected}:
            selected.append(row)
    weights = [50, 30]
    for idx, row in enumerate(selected):
        row["recommended_weight"] = weights[idx] if len(selected) > 1 else 50
    return selected


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_blog(payload: dict[str, Any]) -> Path:
    run_date = payload["run_date"]
    rows = payload["core_pool"][:20]
    rec = payload["recommendations"]
    path = RESEARCH_DIR / f"{run_date}-etf-rotation-pool.md"
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    def fmt_row(r: dict[str, Any]) -> str:
        prem = r.get("premium") or {}
        prem_text = f"{prem.get('premium'):.2f}%" if prem.get("available") else "缺失"
        return f"- 🔴 {r['name']} `{r['code']}`：综合分 {r['score']}，20日 {r['ret20']}%，3日 {r['ret3']}%，折溢价 {prem_text}，20日日均成交额 {r['avg_amount20']/1e8:.2f}亿"
    content = f"""---
title: 'ETF轮动池夜间筛选：{run_date}'
description: '全市场ETF按双动量、流动性、折溢价和风险过滤生成次日轮动池。'
pubDate: {run_date}
category: '研测'
---

## 结论

- 数据生成时间：{payload['generated_at']}
- ETF universe：{payload['summary']['universe_count']}只（来源：{payload['summary']['universe_source']}）
- 有效K线：{payload['summary']['valid_count']}只
- 核心轮动池：{payload['summary']['core_count']}只
- 观察池：{payload['summary']['watch_count']}只
- 排除：{payload['summary']['excluded_count']}只
- Top 2：{', '.join([f"{x['name']} {x['code']}" for x in rec]) or '空仓/货币ETF'}

## 策略参数

- MA周期：20日均线
- 趋势窗口：5日
- 短期天数：3日
- 短期阈值：> -5%
- 排序：综合分优先，20日收益为主因子
- 流动性：20日日均成交额 ≥ 3000万元
- 折溢价：≤ 1.5%
- 风险过滤：20日收益 ≤ 25%、3日收益 ≤ 8%、20日高点回撤 ≤ 12%、近3日成交额/近20日成交额 ≥ 0.8

## Top 20 核心候选

{chr(10).join(fmt_row(r) for r in rows) if rows else '- 当前无核心候选。'}

## 执行口径

晚上20:00生成夜间轮动池；盘中页面读取该池子，再用实时行情刷新当前价、涨跌幅、折溢价和排序。跨境ETF溢价高于1.0%降级观察，高于1.5%排除。
"""
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    start = now_cn()
    universe, source, source_total = fetch_universe()
    rows: list[dict[str, Any]] = []
    max_workers = 10 if source == "eastmoney" else 8
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(calc_one, x) for x in universe]
        for idx, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(fut.result())
            if source == "eastmoney" and idx % 100 == 0:
                time.sleep(1)
    valid = [x for x in rows if "price" in x]
    core = sorted([x for x in valid if x.get("status") == "core"], key=lambda r: r.get("score", -999), reverse=True)
    watch = sorted([x for x in valid if x.get("status") == "watch"], key=lambda r: r.get("score", -999), reverse=True)
    excluded = [x for x in rows if x.get("status") == "excluded"]
    recommendations = assign_recommendations(core)
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S %Z")
    run_date = now_cn().date().isoformat()
    payload = {
        "generated_at": generated_at,
        "run_date": run_date,
        "params": PARAMS,
        "summary": {
            "universe_source": source,
            "universe_count": len(universe),
            "source_total": source_total,
            "valid_count": len(valid),
            "core_count": len(core),
            "watch_count": len(watch),
            "excluded_count": len(excluded),
            "liquidity_pass_count": sum(1 for x in valid if x.get("checks", {}).get("liquidity")),
            "momentum_pass_count": sum(1 for x in valid if x.get("checks", {}).get("momentum")),
            "premium_pass_count": sum(1 for x in valid if x.get("checks", {}).get("premium")),
        },
        "recommendations": recommendations,
        "core_pool": core,
        "watch_pool": watch[:100],
        "excluded_sample": excluded[:100],
    }
    write_json(PUBLIC_DATA / "etf-momentum-latest.json", payload)
    write_json(PUBLIC_DATA / "etf-rotation-pool.json", {"generated_at": generated_at, "items": core})
    write_json(PUBLIC_DATA / "etf-screening-report.json", payload)
    blog_path = write_blog(payload)
    elapsed = (now_cn() - start).total_seconds()
    print(f"ETF轮动池生成完成：核心{len(core)}只，观察{len(watch)}只，Universe {len(universe)}只，耗时{elapsed:.0f}s，博客 {blog_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
