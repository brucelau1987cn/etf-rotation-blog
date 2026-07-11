#!/usr/bin/env python3
"""Enrich A-share garden recommendations with grounded pool price levels.

The prose recommendation file remains the editorial source of truth. Numeric levels
come only from generate_garden_pool.py and are never inferred from prose. Writes are
atomic; malformed or insufficient pool data leaves the previous output untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOMMENDATIONS = ROOT / "public/data/garden-recommendations.json"
POOL = ROOT / "public/data/etf-garden-pool.json"
SECTIONS = ("harvest", "plant")
REQUIRED_POOL_ROWS = 60


def read_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return None


def technical_levels(row: dict[str, Any]) -> tuple[float | None, float | None, float | None, str, str]:
    """Return stored ATR levels, or a deterministic no-ATR fallback for new ETFs."""
    support, target, stop = (numeric(row.get(k)) for k in ("support", "target", "stop"))
    if support is not None and target is not None and stop is not None:
        return support, target, stop, str(row.get("level_basis") or "ATR14"), str(row.get("level_model_version") or "atr14-v1")
    price, ma20, high = numeric(row.get("price")), numeric(row.get("ma20")), numeric(row.get("high"))
    if price is None:
        return None, None, None, "unavailable", "unavailable"
    support = min(price, ma20) if ma20 is not None else price
    target = max(high or price, price * 1.03)
    stop = min(support * 0.98, price * 0.98)
    return round(support, 4), round(target, 4), round(stop, 4), "MA20/现价支撑 +3%目标 -2%失效（无ATR回退）", "ma20-fallback-v1"


def gap(price: float | None, level: float | None) -> float | None:
    if price is None or level is None or price == 0:
        return None
    return round((level / price - 1) * 100, 2)


def build_payload(recommendations: dict[str, Any], pool: dict[str, Any]) -> tuple[dict[str, Any], int]:
    rows = pool.get("all_rows")
    if not isinstance(rows, list):
        raise ValueError("pool all_rows is missing")
    valid_rows = [row for row in rows if isinstance(row, dict) and row.get("code") and numeric(row.get("price")) is not None]
    if len(valid_rows) < REQUIRED_POOL_ROWS:
        raise ValueError(f"insufficient valid pool rows: {len(valid_rows)}/{REQUIRED_POOL_ROWS}")
    row_map = {str(row["code"]): row for row in valid_rows}

    result = json.loads(json.dumps(recommendations, ensure_ascii=False))
    matched = 0
    for section in SECTIONS:
        items = result.get(section)
        if not isinstance(items, list):
            raise ValueError(f"recommendations.{section} must be a list")
        for item in items:
            if not isinstance(item, dict) or not item.get("code"):
                raise ValueError(f"invalid item in {section}")
            row = row_map.get(str(item["code"]))
            if not row:
                for key in ("price", "support", "target", "stop", "action_level", "trigger_level", "distance_pct"):
                    item[key] = None
                item["level_status"] = "unmatched"
                continue
            matched += 1
            is_harvest = section == "harvest"
            price = numeric(row.get("price"))
            support, target, stop, basis, level_version = technical_levels(row)
            action_level = target if is_harvest else support
            distance = gap(price, action_level)
            item.update({
                "price": price,
                "support": support,
                "target": target,
                "stop": stop,
                "action_level": action_level,
                "trigger_level": action_level,
                "action_level_label": "目标位" if is_harvest else "回踩位",
                "distance_pct": distance,
                "trend_level": row.get("strength_level"),
                "risk_level": row.get("risk_level"),
                "price_date": row.get("date"),
                "level_basis": basis,
                "model_version": level_version,
                "data_source": row.get("quote_source") or pool.get("quote_source"),
                "level_status": "ready" if all(v is not None for v in (price, support, target, stop)) else "partial",
            })
    result["level_model_version"] = "A ETF Garden Levels v1"
    result["level_data_as_of"] = pool.get("latest_trade_date")
    result["level_generated_at"] = pool.get("generated_at")
    result["level_source"] = pool.get("quote_source")
    return result, matched


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true", help="validate inputs and enrichment without writing")
    args = parser.parse_args()
    original = read_object(RECOMMENDATIONS)
    pool = read_object(POOL)
    payload, matched = build_payload(original, pool)
    total = sum(len(payload.get(section, [])) for section in SECTIONS)
    if matched != total:
        raise ValueError(f"recommendation codes unmatched: {matched}/{total}")
    if not args.validate:
        atomic_write(RECOMMENDATIONS, payload)
    mode = "validated" if args.validate else "enriched"
    print(json.dumps({"status": mode, "matched": matched, "total": total, "level_date": payload.get("level_data_as_of")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
