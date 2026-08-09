#!/usr/bin/env python3
"""Push low-chip stock metrics to D1 via the low-chip-metrics API.

Reads public/data/a-low-chip-stocks.json and POSTs each stock's
shareholder_metrics to the D1-backed API endpoint.

Usage:
  python3 scripts/sync_low_chip_to_d1.py [--date YYYY-MM-DD] [--endpoint URL]

Env:
  LOW_CHIP_SYNC_TOKEN — Bearer token matching wrangler.toml [vars]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
TOKEN = os.environ.get("LOW_CHIP_SYNC_TOKEN") or "42TcgHQjub15gVGy2EQ-FHXoTlAaMH1IEpCcM2kZALE"
ENDPOINT = "https://etf.peekabo.cc/api/public/v1/low-chip-metrics"


def post_metrics(metrics: list[dict]) -> dict:
    """POST metrics array to the D1 API endpoint."""
    payload = json.dumps({"metrics": metrics}, ensure_ascii=False)
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", ENDPOINT,
         "-H", f"Authorization: Bearer {TOKEN}",
         "-H", "Content-Type: application/json",
         "-d", payload],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": r.stdout[:200], "raw": True}


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    data_as_of = payload.get("data_as_of") or ""
    codes = payload.get("intersection") or []
    enrichments = payload.get("enrichments") or {}

    if not codes or not enrichments:
        print(f"no intersection data ({data_as_of}), skipping", flush=True)
        return 0

    metrics = []
    for code in codes:
        enr = enrichments.get(code)
        if not enr:
            continue
        sm = enr.get("shareholder_metrics") or {}
        stock_name = enr.get("name") or enr.get("stock_name") or ""
        metrics.append({
            "trade_date": data_as_of,
            "stock_code": code,
            "stock_name": stock_name,
            "shareholder_count": sm.get("shareholder_count"),
            "shareholder_change_pct": sm.get("shareholder_change_pct"),
            "main_force": sm.get("main_force"),
            "main_force_label": sm.get("main_force_label"),
            "concentration90": sm.get("concentration90"),
            "top10_float_ratio": sm.get("top10_float_ratio"),
            "price": sm.get("price"),
        })

    if not metrics:
        print("no metrics to push", flush=True)
        return 0

    result = post_metrics(metrics)
    if result.get("ok"):
        print(f"D1 sync: {result.get('inserted')}/{result.get('total')} inserted ({data_as_of})", flush=True)
    else:
        print(f"D1 sync FAILED: {result.get('error')}", flush=True)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())