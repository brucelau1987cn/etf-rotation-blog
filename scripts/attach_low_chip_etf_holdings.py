#!/usr/bin/env python3
"""Attach ETF-holding data per low-chip stock via mx-data (Eastmoney Miaoxiang).

Replace iWenCai's holding-ETF query. mx-data returns fund-holdings table
with 基金代码/简称/持股数量/持股市值; filter by suffix .SH/.SZ (ETF)
vs .OF (off-exchange fund, skip).

Failure-soft: HTTP/query error -> etf_holdings=[] + etf_top_category=null.
Outputs atomic JSON write with separators=(",", ":") + ensure_ascii=False.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

TOP_N_HOLDINGS = 5
MAX_WORKERS = 1   # mx-data 限额 code=112: 并发>1 触发限流
MX_QUERY = "{code} 持有ETF 基金持股统计"
RATE_LIMIT_CODES = (112,)
MAX_RETRIES = 2
RETRY_BACKOFF_S = 5.0

_mx_client = None


def _get_mx():
    global _mx_client
    if _mx_client is None:
        sys.path.insert(0, "/root/.hermes/skills/mx-data")
        from mx_data import MXData
        api_key = os.getenv("MX_APIKEY")
        if not api_key:
            raise RuntimeError("MX_APIKEY env var not set")
        _mx_client = MXData(api_key=api_key)
    return _mx_client


def _to_yi(value_str: str) -> float | None:
    if not value_str:
        return None
    s = str(value_str).strip()
    m = re.match(r'^([\d.]+)\s*亿', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([\d.]+)\s*万', s)
    if m:
        return float(m.group(1)) / 10000.0
    m = re.match(r'^([\d.]+)$', s)
    if m:
        v = float(m.group(1))
        if v >= 1e9:
            return v / 1e8
        return v
    return None


def _to_shares(value_str: str) -> float | None:
    if not value_str:
        return None
    s = str(value_str).strip()
    m = re.match(r'^([\d.]+)\s*亿', s)
    if m:
        return float(m.group(1)) * 1e8
    m = re.match(r'^([\d.]+)\s*万', s)
    if m:
        return float(m.group(1)) * 1e4
    m = re.match(r'^([\d.]+)$', s)
    if m:
        return float(m.group(1))
    return None


def parse_mx_etf_holdings(tables: list[dict], full_code: str) -> list[dict]:
    target_sheet = None
    for t in tables:
        if f"({full_code})" in t.get("sheet_name", ""):
            target_sheet = t
            break
    if not target_sheet:
        return []

    rows = target_sheet["rows"]
    if not rows or len(rows) < 3:
        return []
    name_row = rows[0]
    shares_row = rows[1] if len(rows) > 1 else {}
    value_row = rows[2] if len(rows) > 2 else {}

    etf_rows: list[dict] = []
    for fund_code, fund_name in name_row.items():
        if fund_code == "date":
            continue
        if not (fund_code.endswith(".SH") or fund_code.endswith(".SZ")):
            continue
        shares = _to_shares(shares_row.get(fund_code, ""))
        value_yi = _to_yi(value_row.get(fund_code, ""))
        if shares is None or value_yi is None:
            continue
        etf_rows.append({
            "code": fund_code,
            "name": str(fund_name),
            "full_name": str(fund_name),
            "weight_pct": None,
            "rank": None,
            "holding_value": value_yi,
            "holding_qty": shares,
            "is_heavy": None,
            "etf_category_l1": "ETF",
            "etf_category_l2": None,
            "fund_manager": None,
            "_market_value_yi": value_yi,
        })

    etf_rows.sort(key=lambda x: x["_market_value_yi"], reverse=True)
    for i, row in enumerate(etf_rows, 1):
        row["rank"] = i
        del row["_market_value_yi"]
    return etf_rows[:TOP_N_HOLDINGS]


def top_category(holdings: list[dict]) -> str | None:
    if not holdings:
        return None
    return "ETF"


def attach_for_one(full_code: str) -> tuple[list[dict], str | None, int]:
    bare = full_code.split(".")[0]
    mx = _get_mx()
    q = MX_QUERY.format(code=bare)
    last_err: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = mx.query(q)
        except Exception as exc:
            last_err = f"exception: {exc}"
            time.sleep(RETRY_BACKOFF_S)
            continue
        tables, _, _, err = mx.parse_result(r)
        if err is None:
            holdings = parse_mx_etf_holdings(tables, full_code)
            return holdings, top_category(holdings), len(holdings)
        # 检查是否为限流错误
        if any(f"状态码 {c}" in err for c in RATE_LIMIT_CODES):
            last_err = err
            print(f"[{full_code}] rate-limited (attempt {attempt+1}/{MAX_RETRIES+1}): {err}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_S * (attempt + 1))
            continue
        print(f"[{full_code}] mx-data parse error: {err}", file=sys.stderr)
        return [], None, 0
    print(f"[{full_code}] gave up after {MAX_RETRIES+1} attempts: {last_err}", file=sys.stderr)
    return [], None, 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DATA))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"FATAL: input file not found: {src}", file=sys.stderr)
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    symbols = payload.get("intersection") or []
    enrichments = payload.setdefault("enrichments", {})
    if not symbols:
        print("intersection is empty, nothing to do")
        return 0

    success = 0
    empty = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(attach_for_one, symbols))
    for i, (code, (holdings, cat, raw_n)) in enumerate(zip(symbols, results), 1):
        rec = enrichments.setdefault(code, {})
        rec["etf_holdings"] = holdings
        rec["etf_top_category"] = cat
        if raw_n == 0:
            empty += 1
            print(f"[{i}/{len(symbols)}] {code}: 0 ETFs")
        else:
            success += 1
            top = holdings[0] if holdings else None
            print(f"[{i}/{len(symbols)}] {code}: {raw_n} ETFs, top={top['name'] if top else '-'} ({top['code'] if top else '-'})")

    if not args.dry_run:
        tmp = src.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(src)
    print(f"\nETF holdings attach (mx-data): {success} ok, {empty} empty, total {len(symbols)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
