#!/usr/bin/env python3
"""Deterministic futures compass maintenance entrypoint for cron."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from futures_compass_data import (
    PUBLIC_SNAPSHOT,
    atomic_json,
    fetch_daily_bars,
    fetch_realtime,
    fetch_warehouse_receipts,
    run_iwencai_review,
)

ROOT = Path(__file__).resolve().parents[1]
CN = ZoneInfo("Asia/Shanghai")


def calendar_trading_day(day: str) -> tuple[bool | None, str]:
    """Reuse the project's authoritative CN exchange-calendar lookup."""
    try:
        from check_a_share_cron_gate import is_trading_day
    except ModuleNotFoundError:
        from scripts.check_a_share_cron_gate import is_trading_day
    return is_trading_day(day)


MAX_CALENDAR_LOOKAHEAD_DAYS = 31
MAX_NORMAL_NIGHT_GAP_DAYS = 3


def next_trading_day(current_day) -> tuple[Any | None, str]:
    """Find the first open CN trading date strictly after current_day."""
    sources = []
    for offset in range(1, MAX_CALENDAR_LOOKAHEAD_DAYS + 1):
        candidate = current_day + timedelta(days=offset)
        trading_day, source = calendar_trading_day(candidate.isoformat())
        sources.append(source)
        if trading_day is None:
            return None, source
        if trading_day:
            return candidate, source
    return None, sources[-1] if sources else "unavailable"


def slot_calendar_gate(slot: str, *, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(CN)).astimezone(CN)
    current_day = current.date()
    if slot == "night":
        current_is_open, current_source = calendar_trading_day(current_day.isoformat())
        if current_is_open is None:
            trading_day, source, calendar_day = None, current_source, current_day
        elif current_is_open is False:
            trading_day, source, calendar_day = False, current_source, current_day
        else:
            calendar_day, source = next_trading_day(current_day)
            if calendar_day is None:
                trading_day = None
                calendar_day = current_day
            else:
                gap_days = (calendar_day - current_day).days
                trading_day = gap_days <= MAX_NORMAL_NIGHT_GAP_DAYS
    else:
        calendar_day = current_day
        trading_day, source = calendar_trading_day(calendar_day.isoformat())
    status = "run" if trading_day is True else "skip" if trading_day is False else "error"
    reason = (
        "exchange calendar is open" if status == "run"
        else "exchange calendar is closed" if status == "skip"
        else "exchange calendar unavailable"
    )
    return {
        "status": status, "reason": reason,
        "calendar_date": calendar_day.isoformat(), "calendar_source": source,
    }


def required_stage_errors(slot: str, result: dict[str, Any]) -> list[str]:
    required = ["review", "briefing", "snapshot"]
    if slot == "day-close":
        required.extend(("daily", "warehouse"))
    errors = []
    for stage in required:
        value = result.get(stage)
        if not isinstance(value, dict):
            errors.append(stage)
        elif stage == "snapshot":
            if value.get("ok") is not True:
                errors.append(stage)
        elif value.get("status") != "ok":
            errors.append(stage)
    return errors


def refresh_briefing() -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_futures_compass_briefing.py")],
        cwd=ROOT, text=True, capture_output=True, timeout=150, check=False,
    )
    if result.returncode != 0:
        return {"status": "error", "detail": (result.stderr or result.stdout)[-500:]}
    return {"status": "ok", "detail": result.stdout.strip()[-500:]}


def run_slot(slot: str) -> dict:
    result = {"review": run_iwencai_review(slot), "briefing": refresh_briefing()}
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
    gate = slot_calendar_gate(args.slot)
    if gate["status"] != "run":
        print(json.dumps({"calendar_gate": gate}, ensure_ascii=False))
        return 0 if gate["status"] == "skip" else 2
    result = run_slot(args.slot)
    result["calendar_gate"] = gate
    failed = required_stage_errors(args.slot, result)
    if failed:
        result["required_stage_errors"] = failed
    print(json.dumps(result, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
