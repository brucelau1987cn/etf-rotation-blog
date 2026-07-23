#!/usr/bin/env python3
"""Strict projection and validation helpers for the buyer-energy transmission contract."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "public/schemas/a-rolling-public.schema.json"
CYCLES = (
    ("PRE", "预备", 105),
    ("A1", "A", 120), ("A2", "A", 135), ("A3", "A", 150), ("A4", "A", 165),
    ("B1", "B", 180), ("B2", "B", 195), ("B3", "B", 210), ("B4", "B", 225),
    ("C1", "C", 240), ("C2", "C", 250), ("C3", "C", 260), ("C4", "C", 270), ("C5", "C", 280), ("C6", "C", 290),
    ("D1", "D", 300), ("D2", "D", 310), ("D3", "D", 320), ("D4", "D", 330), ("D5", "D", 340), ("D6", "D", 350),
    ("E1", "E", 360), ("E2", "E", 370), ("E3", "E", 380), ("E4", "E", 390), ("E5", "E", 400), ("E6", "E", 410),
    ("F1", "F", 420), ("F2", "F", 430), ("F3", "F", 440), ("F4", "F", 450), ("F5", "F", 460), ("F6", "F", 470),
    ("G", "G", 480),
)


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def iso_utc(value: Any, field: str) -> str:
    return parse_timestamp(value, field).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def timeframe_label(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    return f"{hours}小时{f'{remainder}分钟' if remainder else ''}"


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return payload


def schema_errors(payload: Any, schema_path: Path = SCHEMA_PATH) -> list[str]:
    validator = Draft202012Validator(load_schema(schema_path), format_checker=FormatChecker())
    return [f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}" for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))]


def validate_public_payload(payload: Any, schema_path: Path = SCHEMA_PATH) -> dict[str, Any]:
    errors = schema_errors(payload, schema_path)
    if not isinstance(payload, dict):
        raise ValueError("rolling payload root must be an object")
    cycles = payload.get("cycles")
    if isinstance(cycles, list):
        actual = [(row.get("cycle_code"), row.get("segment"), row.get("timeframe_minutes")) for row in cycles if isinstance(row, dict)]
        if tuple(actual) != CYCLES:
            errors.append("cycles must contain the canonical ordered 34-point sequence")
        lit_count = 0
        for index, row in enumerate(cycles):
            if not isinstance(row, dict):
                continue
            triggered_at = row.get("buy_triggered_at")
            state = row.get("buy_state")
            if triggered_at is not None:
                try:
                    parse_timestamp(triggered_at, f"cycles[{index}].buy_triggered_at")
                except ValueError as exc:
                    errors.append(str(exc))
            if state == "BUY":
                lit_count += 1
                if triggered_at is None:
                    errors.append(f"cycles[{index}] BUY requires a trigger time")
            elif triggered_at is not None:
                errors.append(f"cycles[{index}] inactive state cannot have a trigger time")
        transmission = payload.get("transmission") or {}
        if any(row.get("buy_state") != "BUY" for row in cycles[:lit_count]) or any(row.get("buy_state") == "BUY" for row in cycles[lit_count:]):
            errors.append("BUY path must be contiguous from PRE")
        if transmission.get("lit_count") != lit_count:
            errors.append("transmission lit_count mismatch")
        expected_current = cycles[lit_count - 1].get("cycle_code") if lit_count else None
        if transmission.get("current_cycle_code") != expected_current:
            errors.append("transmission current_cycle_code mismatch")
    for field in ("generated_at", "data_as_of"):
        try:
            parse_timestamp(payload.get(field), field)
        except ValueError as exc:
            errors.append(str(exc))
    for index, alert in enumerate(payload.get("sell_alerts") or []):
        try:
            parse_timestamp(alert.get("triggered_at"), f"sell_alerts[{index}].triggered_at")
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))
    return copy.deepcopy(payload)


def project_upstream(upstream: Any, *, generated_at: str, stale_after_seconds: int = 900) -> dict[str, Any]:
    if not isinstance(upstream, dict) or not isinstance(upstream.get("cycles"), list):
        raise ValueError("upstream energy payload requires cycles")
    rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(upstream["cycles"]):
        if not isinstance(row, dict):
            raise ValueError(f"upstream cycle[{index}] must be an object")
        code = row.get("cycle_code")
        if not isinstance(code, str) or not code:
            raise ValueError(f"upstream cycle[{index}] requires cycle_code")
        if code in rows:
            raise ValueError(f"upstream cycle code is duplicated: {code}")
        rows[code] = row
    if set(rows) != {code for code, _, _ in CYCLES}:
        raise ValueError("upstream cycle set is incomplete")
    cycles = []
    for code, segment, minutes in CYCLES:
        raw = rows[code]
        if raw.get("timeframe_minutes") != minutes:
            raise ValueError(f"upstream cycle {code} has invalid minutes")
        state = raw.get("buy_state")
        if state not in {"BUY", "INACTIVE", "UNKNOWN"}:
            raise ValueError(f"upstream cycle {code} has invalid buy_state")
        triggered = iso_utc(raw["buy_triggered_at"], f"{code}.buy_triggered_at") if raw.get("buy_triggered_at") else None
        cycles.append({"cycle_code": code, "segment": segment, "timeframe_minutes": minutes, "timeframe_label": timeframe_label(minutes), "buy_state": state, "buy_triggered_at": triggered, "source": "UPSTREAM_PROJECTION"})
    lit_count = sum(row["buy_state"] == "BUY" for row in cycles)
    if any(row["buy_state"] != "BUY" for row in cycles[:lit_count]) or any(row["buy_state"] == "BUY" for row in cycles[lit_count:]):
        raise ValueError("upstream BUY path must be contiguous from PRE")
    data_as_of = iso_utc(upstream.get("data_as_of") or upstream.get("generated_at"), "upstream.data_as_of")
    generated = iso_utc(generated_at, "generated_at")
    age = max(0, int((parse_timestamp(generated, "generated_at") - parse_timestamp(data_as_of, "data_as_of")).total_seconds()))
    payload = {
        "schema_version": "a-rolling-energy-v2", "mode": "live", "generated_at": generated,
        "data_as_of": data_as_of, "freshness": "fresh" if age <= stale_after_seconds else "stale",
        "stale_after_seconds": stale_after_seconds, "notice": "只读公开投影；买卖信号事实由上游信号系统提供。",
        "delivery": {"state": "live", "reason": None}, "instrument": upstream.get("instrument"),
        "transmission": {"state": "complete" if lit_count == len(CYCLES) else "transmitting" if lit_count else "observing", "basis": "single_run", "current_cycle_code": cycles[lit_count - 1]["cycle_code"] if lit_count else None, "started_at": cycles[0]["buy_triggered_at"] if lit_count else None, "lit_count": lit_count, "continuous_confirmed": True},
        "cycles": cycles, "sell_alerts": upstream.get("sell_alerts") or [],
    }
    return validate_public_payload(payload)


def lkg_payload(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    degraded = validate_public_payload(payload)
    degraded["delivery"] = {"state": "lkg", "reason": reason[:200]}
    return validate_public_payload(degraded)
