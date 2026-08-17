#!/usr/bin/env python3
"""Push low-chip stock metrics to D1 via the low-chip-metrics API.

Reads public/data/a-low-chip-stocks.json (current) and/or every
low-chip-history/*.json snapshot, and POSTs each stock's
shareholder_metrics to the D1-backed API endpoint.

Usage:
  python3 scripts/sync_low_chip_to_d1.py [--date YYYY-MM-DD]   # current file only
  python3 scripts/sync_low_chip_to_d1.py --history             # all snapshots + current
  python3 scripts/sync_low_chip_to_d1.py --day 2026-07-31      # one snapshot file

Env:
  LOW_CHIP_SYNC_TOKEN — Bearer token matching wrangler.toml [vars]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
TOKEN = os.environ.get("LOW_CHIP_SYNC_TOKEN") or ""
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


def snapshot_metrics(payload: dict) -> list[dict]:
    """Extract metrics rows from a snapshot/current payload."""
    data_as_of = payload.get("data_as_of") or ""
    codes = payload.get("intersection") or []
    enrichments = payload.get("enrichments") or {}
    periods = payload.get("periods") or {}
    week_list = periods.get("week") or []
    month_list = periods.get("month") or []
    quarter_list = periods.get("quarter") or []
    # build period lookup by symbol
    def _by_symbol(lst):
        return {r.get("symbol"): r for r in lst if r.get("symbol")}
    week_map = _by_symbol(week_list)
    month_map = _by_symbol(month_list)
    quarter_map = _by_symbol(quarter_list)

    def _period_name(code: str) -> str:
        """Names live on period rows (week/month/quarter), not enrichments."""
        for m in (week_map, month_map, quarter_map):
            row = m.get(code) or {}
            name = row.get("name") or row.get("stock_name")
            if name:
                return str(name).strip()
        return ""

    metrics = []
    for code in codes:
        enr = enrichments.get(code)
        if not enr:
            continue
        sm = enr.get("shareholder_metrics") or {}
        # Prefer periods.name (always present in snapshots); enrichments rarely store name.
        stock_name = (
            _period_name(code)
            or enr.get("name")
            or enr.get("stock_name")
            or ""
        )
        w = week_map.get(code) or {}
        m = month_map.get(code) or {}
        q = quarter_map.get(code) or {}
        # use the first available period record for price/change
        base = w or m or q or {}
        compact_trade_date = "".join(ch for ch in str(data_as_of) if ch.isdigit())[:8]
        compact_stock_code = str(code).split(".")[0].zfill(6)
        metrics.append({
            "trade_date": compact_trade_date,
            "stock_code": compact_stock_code,
            "stock_name": stock_name,
            "shareholder_count": sm.get("shareholder_count"),
            "shareholder_change_pct": sm.get("shareholder_change_pct"),
            "main_force": sm.get("main_force"),
            "main_force_label": sm.get("main_force_label"),
            "chip_focus": sm.get("chip_focus"),
            "report_period": sm.get("report_period"),
            "top10_float_ratio": sm.get("top10_float_ratio"),
            "price": base.get("price") or sm.get("price"),
            "announcement_date": sm.get("announcement_date"),
            "week_profit": w.get("value"),
            "month_profit": m.get("value"),
            "quarter_profit": q.get("value"),
            "change_percent": base.get("change_percent"),
            "industry": enr.get("industry"),
            "sector": enr.get("sector"),
            "financials": enr.get("financials"),
            "theme_concepts": enr.get("theme_concepts") or enr.get("theme_concept"),
            "quality_shareholder": 1 if enr.get("quality_shareholder") else 0,
            "shareholder_nature": {
                "report_period": enr.get("shareholder_nature_report_period"),
                "quality_shareholder": bool(enr.get("quality_shareholder")),
                "quality_shareholder_names": enr.get("quality_shareholder_names") or [],
                "institutional_shareholder": bool(enr.get("institutional_shareholder")),
                "institutional_shareholder_names": enr.get("institutional_shareholder_names") or [],
            },
        })
    return metrics


def push(payload: dict) -> int:
    metrics = snapshot_metrics(payload)
    if not metrics:
        source = payload.get("_source") or "current"
        print(f"no metrics to push ({source})", flush=True)
        return 0
    result = post_metrics(metrics)
    label = payload.get("data_as_of") or payload.get("_source") or ""
    if result.get("ok"):
        print(f"D1 sync: {result.get('inserted')}/{result.get('total')} inserted ({label})", flush=True)
        return 0
    else:
        print(f"D1 sync FAILED ({label}): {result.get('error')}", flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="push current file only (default)")
    parser.add_argument("--history", action="store_true", help="push all history snapshots + current")
    parser.add_argument("--day", help="push a single history snapshot by date YYYY-MM-DD")
    args = parser.parse_args()

    failures = 0
    if args.history:
        snapshots = sorted(HISTORY_DIR.glob("????-??-??.json"))
        for snap in snapshots:
            payload = json.loads(snap.read_text(encoding="utf-8"))
            payload["_source"] = snap.name
            failures += push(payload)
        # also current file
        payload = json.loads(DATA.read_text(encoding="utf-8"))
        payload["_source"] = "current"
        failures += push(payload)
    elif args.day:
        snap = HISTORY_DIR / f"{args.day}.json"
        if not snap.exists():
            print(f"snapshot not found: {args.day}", flush=True)
            return 1
        payload = json.loads(snap.read_text(encoding="utf-8"))
        payload["_source"] = snap.name
        failures += push(payload)
    else:
        payload = json.loads(DATA.read_text(encoding="utf-8"))
        payload["_source"] = "current"
        failures += push(payload)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())