#!/usr/bin/env python3
"""Strict projection and validation helpers for the public rolling-signal contract."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "public/schemas/a-rolling-public.schema.json"
TIMEFRAMES = ("10m", "30m", "1h", "2h", "3h", "4h", "5h", "6h", "1D")
DIRECTIONS = {"BUY", "SELL", "UNKNOWN", "CONFLICT"}
SIGNAL_FIELDS = (
    "instrument_name", "exchange", "symbol", "timeframe", "direction",
    "latest_signal_at", "duration", "phase_code", "phase_label",
    "alert_configured_count", "live_verified_count",
)


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def iso_utc(value: Any, field: str) -> str:
    parsed = parse_timestamp(value, field).astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return payload


def schema_errors(payload: Any, schema_path: Path = SCHEMA_PATH) -> list[str]:
    validator = Draft202012Validator(load_schema(schema_path), format_checker=FormatChecker())
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    ]


def validate_public_payload(payload: Any, schema_path: Path = SCHEMA_PATH) -> dict[str, Any]:
    errors = schema_errors(payload, schema_path)
    if not isinstance(payload, dict):
        raise ValueError("rolling payload root must be an object")
    signals = payload.get("signals")
    if isinstance(signals, list):
        timeframes = [row.get("timeframe") for row in signals if isinstance(row, dict)]
        if tuple(timeframes) != TIMEFRAMES:
            errors.append(f"signals must contain the ordered unique timeframe set: {list(TIMEFRAMES)}")
        identities = {
            (row.get("exchange"), row.get("symbol"), row.get("instrument_name"))
            for row in signals if isinstance(row, dict)
        }
        if len(identities) != 1:
            errors.append("signals must describe exactly one instrument")
        for index, row in enumerate(signals):
            if not isinstance(row, dict):
                continue
            latest = row.get("latest_signal_at")
            if latest is not None:
                try:
                    parse_timestamp(latest, f"signals[{index}].latest_signal_at")
                except ValueError as exc:
                    errors.append(str(exc))
            if row.get("live_verified_count", 0) > row.get("alert_configured_count", 0):
                errors.append(f"signals[{index}] live_verified_count exceeds alert_configured_count")
    for field in ("generated_at", "data_as_of"):
        try:
            parse_timestamp(payload.get(field), field)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))
    return copy.deepcopy(payload)


def project_upstream(
    upstream: Any,
    *,
    generated_at: str,
    stale_after_seconds: int = 900,
) -> dict[str, Any]:
    if not isinstance(upstream, dict):
        raise ValueError("upstream rolling payload root must be an object")
    raw_signals = upstream.get("signals")
    if not isinstance(raw_signals, list):
        raise ValueError("upstream rolling payload requires signals")
    by_timeframe: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_signals):
        if not isinstance(row, dict):
            raise ValueError(f"upstream signal[{index}] must be an object")
        timeframe = row.get("timeframe")
        if timeframe not in TIMEFRAMES or timeframe in by_timeframe:
            raise ValueError(f"upstream timeframe is missing, unsupported or duplicated: {timeframe!r}")
        missing = [field for field in SIGNAL_FIELDS if field not in row]
        if missing:
            raise ValueError(f"upstream signal[{index}] missing fields: {missing}")
        direction = str(row.get("direction") or "").upper()
        if direction not in DIRECTIONS:
            raise ValueError(f"upstream signal[{index}] has invalid direction")
        projected = {field: row[field] for field in SIGNAL_FIELDS}
        projected["direction"] = direction
        projected["source"] = "UPSTREAM_PROJECTION"
        if projected["latest_signal_at"] is not None:
            projected["latest_signal_at"] = iso_utc(
                projected["latest_signal_at"], f"signals[{index}].latest_signal_at",
            )
        for count_field in ("alert_configured_count", "live_verified_count"):
            value = projected[count_field]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"upstream signal[{index}] {count_field} must be an integer")
        by_timeframe[timeframe] = projected
    if set(by_timeframe) != set(TIMEFRAMES):
        missing = sorted(set(TIMEFRAMES) - set(by_timeframe))
        raise ValueError(f"upstream rolling payload is incomplete: {missing}")

    data_as_of = upstream.get("data_as_of") or upstream.get("generated_at")
    data_time = parse_timestamp(data_as_of, "upstream.data_as_of")
    generated_time = parse_timestamp(generated_at, "generated_at")
    age_seconds = max(0, int((generated_time - data_time).total_seconds()))
    freshness = "fresh" if age_seconds <= stale_after_seconds else "stale"
    payload = {
        "schema_version": "a-rolling-public-v1",
        "mode": "live",
        "generated_at": iso_utc(generated_at, "generated_at"),
        "data_as_of": iso_utc(data_as_of, "upstream.data_as_of"),
        "freshness": freshness,
        "stale_after_seconds": stale_after_seconds,
        "notice": "只读公开投影；方向与阶段由上游信号系统提供。",
        "delivery": {"state": "live", "reason": None},
        "signals": [by_timeframe[timeframe] for timeframe in TIMEFRAMES],
    }
    return validate_public_payload(payload)


def lkg_payload(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    validated = validate_public_payload(payload)
    degraded = copy.deepcopy(validated)
    degraded["delivery"] = {"state": "lkg", "reason": reason[:200]}
    return validate_public_payload(degraded)
