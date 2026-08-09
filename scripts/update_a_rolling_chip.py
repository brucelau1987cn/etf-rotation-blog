#!/usr/bin/env python3
"""Refresh A-share rolling chip metrics from the V2 same-calculator endpoint."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INSTRUMENTS_PATH = ROOT / "public/data/a-rolling-instruments.json"
OUTPUT_PATH = ROOT / "public/data/a-rolling-chip.json"
DEFAULT_ENDPOINT = "https://etf.peekabo.cc/api/public/v1/chip"


def load_instruments() -> list[dict]:
    payload = json.loads(INSTRUMENTS_PATH.read_text())
    if isinstance(payload, list):
        return payload
    for key in ("instruments", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ValueError("unsupported rolling instruments payload")


def bare_symbol(item: dict) -> str:
    raw = str(item.get("symbol") or item.get("code") or "")
    return raw.split(".")[0]


def display_name(item: dict) -> str:
    return str(item.get("instrument_name") or item.get("name") or bare_symbol(item))


def fetch_chip(endpoint: str, symbol: str) -> dict:
    query = urllib.parse.urlencode({"symbol": symbol, "adjust": "qfq", "limit": 90, "refresh": 1})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"User-Agent": "HermesRollingChip/2.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    latest = payload.get("latest") or {}
    if payload.get("status") != "ok" or not latest:
        raise RuntimeError(f"chip endpoint failed for {symbol}")
    return payload


def build_payload(endpoint: str) -> dict:
    chips: dict[str, dict] = {}
    failures: dict[str, str] = {}
    as_of: list[str] = []
    for item in load_instruments():
        symbol = bare_symbol(item)
        if not symbol or not symbol.isdigit() or len(symbol) != 6 or not symbol.startswith(("00", "30", "60", "68")):
            continue
        try:
            payload = fetch_chip(endpoint, symbol)
        except Exception as exc:  # preserve successful symbols; expose failures explicitly
            failures[symbol] = str(exc)
            continue
        latest = payload["latest"]
        if payload.get("as_of"):
            as_of.append(str(payload["as_of"]))
        chips[symbol] = {
            "name": display_name(item),
            "adjust": payload.get("adjust", "qfq"),
            "as_of": payload.get("as_of"),
            "profit_ratio": latest["profit_ratio_pct"],
            "concentration90": latest["concentration_90_pct"],
            "avg_cost": latest["average_cost"],
            "profit_ratio_change_pp": payload.get("profit_ratio_change_pp"),
        }
    if not chips:
        raise RuntimeError("all rolling chip requests failed")
    return {
        "schema_version": "a-rolling-chip-v2",
        "data_as_of": max(as_of) if as_of else None,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "source": "edge-quote-api /api/public/v1/chip qfq same-calculator series",
        "failures": failures,
        "chips": chips,
    }


def main() -> None:
    endpoint = os.environ.get("CHIP_ENDPOINT", DEFAULT_ENDPOINT).rstrip("?")
    payload = build_payload(endpoint)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUTPUT_PATH), "count": len(payload["chips"]), "data_as_of": payload["data_as_of"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
