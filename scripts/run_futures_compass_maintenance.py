#!/usr/bin/env python3
"""Deterministic futures compass maintenance entrypoint for cron."""
from __future__ import annotations

import argparse
import json
from futures_compass_data import fetch_daily_bars, fetch_warehouse_receipts, run_iwencai_review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=["preopen", "day-close", "night"])
    args = parser.parse_args()
    result = {"review": run_iwencai_review(args.slot)}
    if args.slot == "day-close":
        result["daily"] = fetch_daily_bars()
        result["warehouse"] = fetch_warehouse_receipts()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["review"].get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
