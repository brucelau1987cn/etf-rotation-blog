#!/usr/bin/env python3
"""Query Eastmoney HSF10 shareholder research for all shareholder/chip metrics.

Source: emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax
          gdrs[0] fields:
            HOLDER_TOTAL_NUM     = 股东人数
            TOTAL_NUM_RATIO      = 较上期变化率(%)
            HOLD_FOCUS           = 筹码集中度标签
            FREEHOLD_RATIO_TOTAL = 十大流通股东持股比例合计(%)
            AVG_FREE_SHARES      = 人均流通股数
            END_DATE             = 报告期
            PRICE                = 当日收盘价
Writes /tmp/low_chip_holder_em.json (mapping code -> {holder_total, ratio, focus, ...}).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
OUT = Path("/tmp/low_chip_holder_em.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _exchange(code: str) -> str:
    bare = code.split(".")[0]
    if bare.startswith("6") or bare.startswith("688"):
        return "SH"
    return "SZ"


def fetch(codes: list[str]) -> dict[str, dict]:
    """Fetch all shareholder metrics from Eastmoney HSF10 per stock."""
    result: dict[str, dict] = {}
    for code in codes:
        bare = code.split(".")[0]
        exch = _exchange(code)
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={exch}{bare}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  {code}: fetch failed: {exc}", flush=True)
            continue
        gdrs = data.get("gdrs") or []
        if not gdrs:
            print(f"  {code}: no gdrs data", flush=True)
            continue
        row = gdrs[0]
        holder_total = row.get("HOLDER_TOTAL_NUM")
        total_ratio = row.get("TOTAL_NUM_RATIO")
        focus = row.get("HOLD_FOCUS")
        freehold_ratio = row.get("FREEHOLD_RATIO_TOTAL")
        avg_free = row.get("AVG_FREE_SHARES")
        end_date = str(row.get("END_DATE", ""))[:10] if row.get("END_DATE") else None
        price = row.get("PRICE")
        prev_holder = row.get("HOLDER_TOTAL_NUM")
        if prev_holder is not None and total_ratio is not None and total_ratio != 0:
            prev_holder = round(holder_total / (1 + total_ratio / 100))
        else:
            prev_holder = None
        result[code] = {
            "holder_total": holder_total,
            "previous_holder": prev_holder,
            "total_ratio": total_ratio,
            "focus": focus,
            "freehold_ratio": freehold_ratio,
            "avg_free_shares": avg_free,
            "end_date": end_date,
            "price": price,
        }
        print(f"  {code}: 户数={holder_total} 变化={total_ratio}% 集中度={focus} 流通股东={freehold_ratio}% 报告期={end_date}", flush=True)
    return result


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = list(payload.get("intersection") or [])
    result = fetch(codes)
    OUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"eastmoney holder: {len(result)}/{len(codes)} -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())