#!/usr/bin/env python3
"""回填 THS 同花顺筹码数据到 D1。

A 股部分：
  遍历 chip_shape_stock_selection 日期目录（122 个历史交易日），
  每日全量拉取 selection/v1/list（2209 只，page_size=500），
  将 closing_profit/average_cost/conc70 等写入 stock_metrics（POST low-chip-metrics）。

ETF 部分（GLD/SLV）：
  将 THS 自算的日/周/月获利（etf_profit_history 表）追加今日记录。

Usage:
  python3 scripts/backfill_ths_chip_d1.py                # 全量回填 A 股 + ETF 今日
  python3 scripts/backfill_ths_chip_d1.py --day 2026-08-11   # 单日 A 股
  python3 scripts/backfill_ths_chip_d1.py --etf-only     # 只写 ETF 获利历史
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

TOKEN = os.environ.get("LOW_CHIP_SYNC_TOKEN") or "42TcgHQjub15gVGy2EQ-FHXoTlAaMH1IEpCcM2kZALE"
LOW_CHIP_ENDPOINT = "https://etf.peekabo.cc/api/public/v1/low-chip-metrics"
ETF_HIST_ENDPOINT = "https://etf.peekabo.cc/api/public/v1/etf-profit-history"
THS_BASE = "https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def ths_get(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "backfill-ths-chip/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def list_dates() -> list[str]:
    d = ths_get(f"{THS_BASE}/selection/v1/date/list/all?chip_type=1")
    items = d.get("data", {}).get("list", [])
    dates = []
    for it in items:
        key = it.get("date") or it.get("select_date") or it.get("trade_date")
        if key:
            dates.append(str(key))
    return sorted(set(dates))


def fetch_day(date: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        d = ths_get(f"{THS_BASE}/selection/v1/list?offset_num={offset}&page_size=500"
                    f"&shape_type=1&chip_type=1&sort_field=closing_profit&sort_order=desc"
                    f"&filter_selfstock=0&date={date}")
        batch = d.get("data", {}).get("list", [])
        rows.extend(batch)
        if not batch or len(batch) < 500:
            break
        offset += len(batch)
        time.sleep(0.15)
    return rows


def to_metrics(date: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        st = r.get("stock") or {}
        code = str(st.get("code") or "").zfill(6)
        if not code:
            continue
        out.append({
            "trade_date": date.replace("-", ""),
            "stock_code": code,
            "stock_name": st.get("name"),
            "closing_profit": r.get("closing_profit"),
            "average_cost": r.get("average_cost"),
            "conc70": r.get("seventy_quantile_concentration"),
            "price": r.get("price"),
            "change_percent": r.get("increase"),
        })
    return out


def backfill_a_share(dates: list[str]) -> None:
    for i, date in enumerate(dates):
        try:
            rows = fetch_day(date)
            metrics = to_metrics(date, rows)
            total = len(metrics)
            inserted = 0
            for j in range(0, total, 250):
                batch = metrics[j:j + 250]
                resp = post_json(LOW_CHIP_ENDPOINT, {"metrics": batch})
                n = resp.get("inserted", 0)
                inserted += n
                if resp.get("error"):
                    print(f"  [{date}] batch@{j} ERR {resp['error'][:100]}", flush=True)
                    break
                time.sleep(0.15)
            print(f"[{i + 1}/{len(dates)}] {date}: {total} 只, D1 inserted {inserted}", flush=True)
        except Exception as e:
            print(f"[{date}] EXC {e}", flush=True)
        time.sleep(0.3)


def push_etf_today() -> None:
    """从 precious-inventory.json 读今日 GLD/SLV 三档，追加 etf_profit_history。"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "public", "data", "precious-inventory.json")
    with open(path) as f:
        d = json.load(f)
    ep = d["data"]["etf_profit"]
    records = []
    for key in ("gold", "silver"):
        v = ep["assets"][key]
        records.append({
            "trade_date": ep.get("as_of", "").replace("-", ""),
            "asset": key,
            "day_profit": v.get("day"),
            "week_profit": v.get("week"),
            "month_profit": v.get("month"),
            "price": v.get("price"),
            "source": "ths-kline",
        })
    resp = post_json(ETF_HIST_ENDPOINT, {"records": records})
    print("ETF history POST:", resp, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="单日回填 YYYY-MM-DD")
    ap.add_argument("--etf-only", action="store_true", help="只写 ETF 获利历史")
    args = ap.parse_args()

    if args.etf_only:
        push_etf_today()
        return

    if args.day:
        dates = [args.day]
    else:
        dates = list_dates()
        print(f"THS 日期目录: {len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）", flush=True)
    backfill_a_share(dates)
    if not args.day:
        push_etf_today()


if __name__ == "__main__":
    main()
