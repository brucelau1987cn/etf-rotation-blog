#!/usr/bin/env python3
"""Generate the 71-ETF ETF Garden pool data for the momentum radar page.

Pool source: session_20260605_214833 三张表去重后的 71 个 ETF/LOF 清单
（详见 etf-garden.md 顶部固定页说明；不复用 youth-online 网络源）。

Output:
- public/data/etf-garden-pool.json

K线 / 行情来自 stock-api@2.7.2，结构与 etf-momentum-latest.json 保持一致，
方便 momentum.astro 切换 DATA_URL。
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
OUT_JSON = PUBLIC_DATA / "etf-garden-pool.json"
STOCK_API_PACKAGE = "stock-api@2.7.2"

# 71 个 ETF/LOF 花园池（按三张表累计去重顺序）
# 来源：session_20260605_214833_548581.json [99][103][107] 三段汇总
GARDEN_POOL: list[dict[str, str]] = [
    # 第一张 28
    {"name": "上证50ETF华夏", "code": "510050", "market": "XSHG", "type": "宽基"},
    {"name": "沪深300ETF易方达", "code": "510310", "market": "XSHG", "type": "宽基"},
    {"name": "中证500ETF南方", "code": "510500", "market": "XSHG", "type": "宽基"},
    {"name": "中证1000ETF南方", "code": "512100", "market": "XSHG", "type": "宽基"},
    {"name": "中证2000ETF华泰柏瑞", "code": "563300", "market": "XSHG", "type": "宽基"},
    {"name": "创业板ETF易方达", "code": "159915", "market": "XSHE", "type": "宽基"},
    {"name": "科创50ETF华夏", "code": "588000", "market": "XSHG", "type": "宽基"},
    {"name": "恒生ETF华夏", "code": "159920", "market": "XSHE", "type": "海外"},
    {"name": "恒生科技ETF华夏", "code": "513180", "market": "XSHG", "type": "海外"},
    {"name": "恒生医疗ETF博时", "code": "513060", "market": "XSHG", "type": "海外"},
    {"name": "标普500ETF博时", "code": "513500", "market": "XSHG", "type": "海外"},
    {"name": "纳指ETF广发", "code": "159941", "market": "XSHE", "type": "海外"},
    {"name": "道琼斯ETF鹏华", "code": "513400", "market": "XSHG", "type": "海外"},
    {"name": "标普生物科技ETF嘉实", "code": "159502", "market": "XSHE", "type": "行业"},
    {"name": "德国ETF华安", "code": "513030", "market": "XSHG", "type": "海外"},
    {"name": "法国ETF华安", "code": "513080", "market": "XSHG", "type": "海外"},
    {"name": "日经ETF华夏", "code": "513520", "market": "XSHG", "type": "海外"},
    {"name": "沙特ETF南方", "code": "159329", "market": "XSHE", "type": "海外"},
    {"name": "印度基金LOF", "code": "164824", "market": "XSHE", "type": "海外"},
    {"name": "东南亚科技ETF华泰柏瑞", "code": "513730", "market": "XSHG", "type": "海外"},
    {"name": "中韩半导体ETF华泰柏瑞", "code": "513310", "market": "XSHG", "type": "海外"},
    {"name": "教育ETF博时", "code": "513360", "market": "XSHG", "type": "行业"},
    {"name": "消费ETF汇添富", "code": "159928", "market": "XSHE", "type": "行业"},
    {"name": "酒ETF鹏华", "code": "512690", "market": "XSHG", "type": "行业"},
    {"name": "医药ETF广发", "code": "159938", "market": "XSHE", "type": "行业"},
    {"name": "农业ETF富国", "code": "159825", "market": "XSHE", "type": "行业"},
    {"name": "半导体ETF国联安", "code": "512480", "market": "XSHG", "type": "行业"},
    {"name": "红利ETF易方达", "code": "515180", "market": "XSHG", "type": "行业"},
    # 第二张新增
    {"name": "养殖ETF国泰", "code": "159865", "market": "XSHE", "type": "行业"},
    {"name": "科技ETF华宝", "code": "515000", "market": "XSHG", "type": "行业"},
    {"name": "电子ETF华宝", "code": "515260", "market": "XSHG", "type": "行业"},
    {"name": "游戏ETF华夏", "code": "159869", "market": "XSHE", "type": "行业"},
    {"name": "创新药ETF银华", "code": "159992", "market": "XSHE", "type": "行业"},
    {"name": "航空航天ETF华夏", "code": "159227", "market": "XSHE", "type": "行业"},
    {"name": "房地产ETF南方", "code": "512200", "market": "XSHG", "type": "行业"},
    {"name": "金融地产ETF广发", "code": "159940", "market": "XSHE", "type": "行业"},
    {"name": "可转债ETF博时", "code": "511380", "market": "XSHG", "type": "行业"},
    {"name": "钢铁ETF国泰", "code": "515210", "market": "XSHG", "type": "行业"},
    {"name": "传媒ETF广发", "code": "512980", "market": "XSHG", "type": "行业"},
    {"name": "信息技术ETF广发", "code": "159939", "market": "XSHE", "type": "行业"},
    {"name": "物流ETF银华", "code": "516530", "market": "XSHG", "type": "行业"},
    {"name": "银行ETF华宝", "code": "512800", "market": "XSHG", "type": "行业"},
    {"name": "养老ETF华宝", "code": "516560", "market": "XSHG", "type": "行业"},
    {"name": "电池ETF广发", "code": "159755", "market": "XSHE", "type": "行业"},
    {"name": "化工ETF鹏华", "code": "159870", "market": "XSHE", "type": "行业"},
    {"name": "汽车ETF国泰", "code": "516110", "market": "XSHG", "type": "行业"},
    {"name": "基建ETF银华", "code": "516950", "market": "XSHG", "type": "行业"},
    {"name": "医疗ETF华宝", "code": "512170", "market": "XSHG", "type": "行业"},
    {"name": "军工ETF国泰", "code": "512660", "market": "XSHG", "type": "行业"},
    {"name": "数字经济ETF鹏扬", "code": "560800", "market": "XSHG", "type": "行业"},
    {"name": "计算机ETF天弘", "code": "159998", "market": "XSHE", "type": "行业"},
    {"name": "豆粕ETF华夏", "code": "159985", "market": "XSHE", "type": "商品"},
    {"name": "煤炭ETF国泰", "code": "515220", "market": "XSHG", "type": "行业"},
    {"name": "家电ETF国泰", "code": "159996", "market": "XSHE", "type": "行业"},
    {"name": "证券ETF国泰", "code": "512880", "market": "XSHG", "type": "行业"},
    {"name": "旅游ETF富国", "code": "159766", "market": "XSHE", "type": "行业"},
    # 第三张新增
    {"name": "稀土ETF嘉实", "code": "516150", "market": "XSHG", "type": "行业"},
    {"name": "金融科技ETF华宝", "code": "159851", "market": "XSHE", "type": "行业"},
    {"name": "上证指数ETF富国", "code": "510210", "market": "XSHG", "type": "宽基"},
    {"name": "软件ETF嘉实", "code": "159852", "market": "XSHE", "type": "行业"},
    {"name": "通信ETF国泰", "code": "515880", "market": "XSHG", "type": "行业"},
    {"name": "有色金属ETF南方", "code": "512400", "market": "XSHG", "type": "行业"},
    {"name": "华宝油气LOF", "code": "162411", "market": "XSHE", "type": "商品"},
    {"name": "人工智能ETF华富", "code": "515980", "market": "XSHG", "type": "行业"},
    {"name": "工业母机ETF国泰", "code": "159667", "market": "XSHE", "type": "行业"},
    {"name": "环保ETF广发", "code": "512580", "market": "XSHG", "type": "行业"},
    {"name": "黄金ETF华安", "code": "518880", "market": "XSHG", "type": "商品"},
    {"name": "电力ETF广发", "code": "159611", "market": "XSHE", "type": "行业"},
    {"name": "机器人ETF华夏", "code": "562500", "market": "XSHG", "type": "行业"},
    {"name": "电网设备ETF华夏", "code": "159326", "market": "XSHE", "type": "行业"},
    {"name": "光伏ETF华泰柏瑞", "code": "515790", "market": "XSHG", "type": "行业"},
]

PARAMS = {
    "ma_period": 20,
    "trend_window": 5,
    "short_days": 3,
    "short_threshold": -5.0,
    "sort_period": 20,
    "holding_count": 5,
}

DEFENSIVE_ASSETS = [
    {"name": "银华日利 ETF", "code": "511880", "role": "现金替代"},
    {"name": "华宝添益 ETF", "code": "511990", "role": "现金替代"},
    {"name": "黄金 ETF", "code": "518880", "role": "避险资产"},
]

# 在 71 池中可作为货币替代的两个场内货基（如有）
GARDEN_CASH = {"511880", "511990"}


def market_prefix(market: str) -> str:
    return "SH" if market == "XSHG" else "SZ"


def safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return math.nan


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def now_cn() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def local_today() -> str:
    return now_cn().date().isoformat()


def fetch_klines(item: dict[str, str], count: int = 90) -> list[dict[str, Any]]:
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
    # 最多重试 2 次，应对 npx 并发时偶发返回异常
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            time.sleep(0.5 * (attempt + 1))
            continue
        try:
            out = json.loads(proc.stdout)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
            continue
        # 拍平并判断有效性
        if isinstance(out, list):
            klines_raw = out
        elif isinstance(out, dict):
            klines_raw = out.get("klines") or out.get("data") or out.get("rows") or []
        else:
            time.sleep(0.5 * (attempt + 1))
            continue
        if not klines_raw:
            time.sleep(0.5 * (attempt + 1))
            continue
        # 必须是 list of dict；如果是 list of list 或其他就重试
        if all(isinstance(x, dict) for x in klines_raw) and klines_raw:
            break
        time.sleep(0.5 * (attempt + 1))
    else:
        return []
    out = klines_raw
    # 兼容多种返回：list / {"klines": [...]} / {"data": [...]} / 嵌套 list
    if isinstance(out, list):
        klines = out
    elif isinstance(out, dict):
        klines = out.get("klines") or out.get("data") or out.get("rows") or []
    else:
        klines = []
    # 拍平一层：如果外层是 list-of-list，取首层
    if klines and isinstance(klines[0], list):
        klines = klines[0] if klines and isinstance(klines[0], list) and klines[0] and isinstance(klines[0][0], (dict, list)) else klines
    parsed: list[dict[str, Any]] = []
    for k in klines:
        if not isinstance(k, dict):
            continue
        c = safe_float(k.get("close"))
        d = k.get("date")
        if d and math.isfinite(c):
            parsed.append(
                {
                    "date": str(d),
                    "open": safe_float(k.get("open")),
                    "high": safe_float(k.get("high")),
                    "low": safe_float(k.get("low")),
                    "close": c,
                    "volume": safe_float(k.get("volume")),
                    "source": k.get("source") or "stock-api",
                }
            )
    return sorted(parsed, key=lambda x: x["date"])


def fetch_quotes(items: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    codes = [f"{market_prefix(x['market'])}{x['code']}" for x in items]
    cmd = ["npx", "-y", STOCK_API_PACKAGE, "get-stocks", *codes]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120)
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        out = json.loads(proc.stdout)
    except Exception:
        return {}
    rows = out if isinstance(out, list) else out.get("data") or []
    qmap: dict[str, dict[str, Any]] = {}
    for r in rows:
        code_raw = str(r.get("code", ""))
        if not code_raw:
            continue
        suffix = code_raw[-6:]
        if suffix:
            qmap[suffix] = r
    return qmap


def calc_row(item: dict[str, str], klines: list[dict[str, Any]], quote: dict[str, Any] | None) -> dict[str, Any]:
    required = PARAMS["ma_period"] + PARAMS["trend_window"] + 1
    if len(klines) < required:
        return {
            **item,
            "status": "excluded",
            "exclude_reason": f"K线不足 {len(klines)}/{required}",
            "bars_count": len(klines),
        }
    closes = [k["close"] for k in klines]
    daily_close = closes[-1]
    price = safe_float((quote or {}).get("now"))
    if math.isfinite(price) and price > 0:
        closes[-1] = price
    else:
        price = daily_close
    ma = avg(closes[-PARAMS["ma_period"]:])
    ma_prev = avg(closes[-PARAMS["ma_period"] - PARAMS["trend_window"]: -PARAMS["trend_window"]])
    ret3 = ((price / closes[-1 - PARAMS["short_days"]]) - 1) * 100 if closes[-1 - PARAMS["short_days"]] else math.nan
    ret5 = ((price / closes[-6]) - 1) * 100 if closes[-6] else math.nan
    ret10 = ((price / closes[-11]) - 1) * 100 if closes[-11] else math.nan
    ret20 = ((price / closes[-1 - PARAMS["sort_period"]]) - 1) * 100 if closes[-1 - PARAMS["sort_period"]] else math.nan
    above_ma = price > ma
    ma_rising = ma > ma_prev
    short_ok = ret3 > PARAMS["short_threshold"]
    pass_abs = above_ma and ma_rising and short_ok
    quote_high = safe_float((quote or {}).get("high"))
    quote_low = safe_float((quote or {}).get("low"))
    close_position = 0.5
    if math.isfinite(quote_high) and math.isfinite(quote_low) and quote_high > quote_low:
        close_position = (price - quote_low) / (quote_high - quote_low)
    risk_flags: list[str] = []
    if not above_ma:
        risk_flags.append("跌破20日线")
    if close_position < 0.4:
        risk_flags.append("收盘偏弱")
    if ret5 > 10:
        risk_flags.append("短线过热")
    stock_code = f"{market_prefix(item['market'])}{item['code']}"
    row = {
        **item,
        "market": "sh" if item["market"] == "XSHG" else "sz",
        "source_market": item["market"],
        "stock_code": stock_code,
        "status": "core" if pass_abs else "watch",
        "date": klines[-1]["date"],
        "evaluation_date": local_today(),
        "price": round(price, 4),
        "daily_close": round(daily_close, 4),
        "prev_close": safe_float((quote or {}).get("yesterday")),
        "change_pct": round(safe_float((quote or {}).get("percent")) * 100, 2) if quote else None,
        "high": quote_high,
        "low": quote_low,
        "quote_name": (quote or {}).get("name"),
        "quote_source": (quote or {}).get("source") or "stock-api",
        "kline_source": klines[-1].get("source") or "stock-api",
        "bars_count": len(klines),
        "ret3": round(ret3, 2),
        "ret5": round(ret5, 2),
        "ret10": round(ret10, 2),
        "ret20": round(ret20, 2),
        "ma20": round(ma, 4),
        "ma20_prev": round(ma_prev, 4),
        "close_position": round(max(0, min(1, close_position)), 2),
        "risk_flags": risk_flags,
        "risk_penalty": len(risk_flags),
        "checks": {
            "price_above_ma": above_ma,
            "ma_rising": ma_rising,
            "short_ok": short_ok,
            "momentum": pass_abs,
        },
    }
    return row


def main() -> int:
    start = now_cn()
    print(f"开始生成 ETF花园 71 池数据，共 {len(GARDEN_POOL)} 只")

    kline_map: dict[str, list[dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_klines, item): item["code"] for item in GARDEN_POOL}
        for f in concurrent.futures.as_completed(futs):
            code = futs[f]
            try:
                kline_map[code] = f.result()
            except Exception as exc:
                print(f"  K线失败 {code}: {exc}")
                kline_map[code] = []
            time.sleep(0.05)
    failed = [c for c, ks in kline_map.items() if not ks]
    if failed:
        print(f"⚠️ 初次失败 {len(failed)} 只：{failed[:10]}{'...' if len(failed)>10 else ''}")
        # 串行重试失败项
        for item in GARDEN_POOL:
            if item["code"] in failed:
                kline_map[item["code"]] = fetch_klines(item)
                time.sleep(0.1)

    print(f"K线拉取完成，尝试批量拉行情（{len(GARDEN_POOL)} 只）")
    quotes = fetch_quotes(GARDEN_POOL)

    rows: list[dict[str, Any]] = []
    for item in GARDEN_POOL:
        row = calc_row(item, kline_map.get(item["code"], []), quotes.get(item["code"]))
        rows.append(row)

    # 货币替代单独归类
    for r in rows:
        if r.get("code") in GARDEN_CASH:
            r["status"] = "cash"

    rows.sort(key=lambda r: -r.get("ret20", -9999))
    for idx, row in enumerate(rows, start=1):
        row["momentum_rank"] = idx
    core = [x for x in rows if x.get("status") == "core"]
    watch = [x for x in rows if x.get("status") == "watch"]
    excluded = [x for x in rows if x.get("status") == "excluded"]
    cash = [x for x in rows if x.get("status") == "cash"]

    recommendations: list[dict[str, Any]] = []
    weights = [25, 20, 15, 15, 10]
    for idx, r in enumerate(core[: PARAMS["holding_count"]]):
        rec = dict(r)
        rec["recommended_weight"] = weights[idx] if idx < len(weights) else 10
        rec["action"] = "加仓" if idx == 0 else "持有"
        recommendations.append(rec)

    latest_trade_date = max((x.get("date", "") for x in rows if x.get("date")), default="2026-06-05")
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S UTC+08:00")

    payload = {
        "generated_at": generated_at,
        "run_date": local_today(),
        "evaluation_date": local_today(),
        "latest_trade_date": latest_trade_date,
        "source_page": "etf-garden-pool-local",
        "pool_source": "ETF花园 71 池 (session_20260605_214833 筛选池, stock-api@2.7.2)",
        "quote_source": "stock-api@2.7.2",
        "kline_source": "stock-api@2.7.2",
        "params": PARAMS,
        "summary": {
            "universe_source": "ETF花园 71 池",
            "universe_count": len(GARDEN_POOL),
            "valid_count": len([x for x in rows if "price" in x]),
            "core_count": len(core),
            "watch_count": len(watch),
            "excluded_count": len(excluded),
            "momentum_pass_count": len(core),
        },
        "market_regime": {
            "state": "震荡" if len(core) >= 5 else "防御",
            "strong_count": len(core),
            "equity_allocation": "30%-50%" if len(core) >= 5 else "10%-30%",
            "defense_allocation": "30%-40%" if len(core) >= 5 else "50%-70%",
            "defensive_assets": DEFENSIVE_ASSETS,
        },
        "defensive_assets": DEFENSIVE_ASSETS,
        "recommendations": recommendations,
        "core_pool": core,
        "watch_pool": watch,
        "cash_pool": cash,
        "excluded_sample": excluded,
        "all_rows": rows,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elapsed = (now_cn() - start).total_seconds()
    print(
        f"✅ 完成：71 池中有效 {payload['summary']['valid_count']}，动量通过 {len(core)}，"
        f"推荐 {len(recommendations)}，耗时 {elapsed:.0f}s，输出 {OUT_JSON.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
