#!/usr/bin/env python3
"""Deterministic futures compass maintenance entrypoint for cron."""
from __future__ import annotations

import argparse
import json
from futures_compass_data import (
    PUBLIC_SNAPSHOT,
    atomic_json,
    fetch_daily_bars,
    fetch_realtime,
    fetch_warehouse_receipts,
    run_iwencai_review,
)


def run_slot(slot: str) -> dict:
    result = {"review": run_iwencai_review(slot)}
    if slot == "day-close":
        result["daily"] = fetch_daily_bars()
        result["warehouse"] = fetch_warehouse_receipts()
    snapshot = fetch_realtime()
    atomic_json(PUBLIC_SNAPSHOT, snapshot)
    result["snapshot"] = snapshot
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=["preopen", "day-close", "night"])
    args = parser.parse_args()
    result = run_slot(args.slot)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["review"].get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
