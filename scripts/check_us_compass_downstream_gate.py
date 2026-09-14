#!/usr/bin/env python3
"""Fail-closed publication/date gate for US Compass downstream jobs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
STATE = Path("/root/.hermes/state/us-etf-close-publisher.json")
NY = ZoneInfo("America/New_York")
BUILD_PYTHON = ROOT / ".build-venv/bin/python"


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def latest_history_date(payload: dict[str, Any]) -> str:
    rows = payload.get("history")
    if not isinstance(rows, list):
        rows = payload.get("records")
    dates = [str(row.get("date") or "") for row in rows or [] if isinstance(row, dict) and row.get("date")]
    return max(dates, default="")


def expected_completed_trade_date(now: datetime | None = None) -> str:
    """Return the latest XNYS session whose scheduled close has elapsed."""
    import exchange_calendars as xcals

    current = now or datetime.now(NY)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_utc = current.astimezone(timezone.utc)
    start = (current_utc.date() - timedelta(days=14)).isoformat()
    end = current_utc.date().isoformat()
    schedule = xcals.get_calendar("XNYS").schedule.loc[start:end]
    completed = schedule[schedule["close"] <= current_utc]
    if completed.empty:
        raise ValueError("XNYS calendar has no completed session in lookup window")
    return completed.index[-1].date().isoformat()


def evaluate(
    *, data_dir: Path = DATA, state_path: Path = STATE,
    require_history: bool = False, now: datetime | None = None,
) -> dict[str, Any]:
    try:
        expected = expected_completed_trade_date(now)
    except (LookupError, ValueError) as exc:
        return {"decision": "blocked", "reason": f"XNYS calendar unavailable: {exc}"}
    try:
        pool = read_object(data_dir / "us-etf-pool.json", "pool")
        garden = read_object(data_dir / "us-etf-garden.json", "garden")
        state = read_object(state_path, "publisher state")
    except ValueError as exc:
        return {"decision": "blocked", "reason": str(exc), "expected_trade_date": expected}
    trade_date = str(pool.get("model_date") or "")
    if not trade_date:
        return {"decision": "blocked", "reason": "pool model_date is missing", "expected_trade_date": expected}
    if trade_date != expected:
        return {
            "decision": "blocked",
            "reason": "pool model_date differs from latest completed XNYS session",
            "trade_date": trade_date,
            "expected_trade_date": expected,
        }
    if pool.get("session_state") != "closed":
        return {"decision": "blocked", "reason": "pool session_state must be closed", "trade_date": trade_date, "expected_trade_date": expected}
    if garden.get("date") != trade_date or garden.get("stage") != "美股收盘版" or garden.get("session_state") != "closed":
        return {"decision": "blocked", "reason": "garden close identity differs from pool", "trade_date": trade_date, "expected_trade_date": expected}
    state_ready = (
        state.get("phase") in {"published", "idempotent"}
        and state.get("trade_date") == trade_date
        and state.get("verified") is True
        and state.get("deployed") is True
    )
    if not state_ready:
        reason = "upstream publication failed" if state.get("phase") == "error" else "publisher state is not verified for expected trade_date"
        return {"decision": "blocked", "reason": reason, "trade_date": trade_date, "expected_trade_date": expected}
    if require_history:
        try:
            history = read_object(data_dir / "us-etf-flower-history.json", "history")
        except ValueError as exc:
            return {"decision": "blocked", "reason": str(exc), "trade_date": trade_date, "expected_trade_date": expected}
        history_date = latest_history_date(history)
        if history_date != trade_date:
            return {"decision": "blocked", "reason": "history date differs from published trade_date", "trade_date": trade_date, "expected_trade_date": expected}
    return {"decision": "run", "reason": "verified publication matches latest completed XNYS session", "trade_date": trade_date, "expected_trade_date": expected}


def main(argv: list[str] | None = None) -> int:
    if importlib.util.find_spec("exchange_calendars") is None:
        if BUILD_PYTHON.exists() and Path(sys.executable).resolve() != BUILD_PYTHON.resolve():
            os.execv(str(BUILD_PYTHON), [str(BUILD_PYTHON), str(Path(__file__).resolve()), *(argv or sys.argv[1:])])
        print(json.dumps({"decision": "blocked", "reason": "XNYS calendar dependency unavailable"}))
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--require-history", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate(data_dir=args.data_dir, state_path=args.state, require_history=args.require_history)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["decision"] == "run" else 2


if __name__ == "__main__":
    raise SystemExit(main())