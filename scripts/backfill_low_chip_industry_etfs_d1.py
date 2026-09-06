#!/usr/bin/env python3
"""Backfill and verify historical industry-ETF mappings in D1.

Run after the Pages function containing the industry_etfs columns is deployed.
The command syncs every canonical low-chip snapshot, then reads every date back
and verifies the mapping status, 91-pool count, and exact ETF candidates.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "public/data/low-chip-history"
SYNC_SCRIPT = ROOT / "scripts/sync_low_chip_to_d1.py"
ENV_FILE = Path("/root/.hermes/credentials/low-chip-sync.env")
ENDPOINT = "https://etf.peekabo.cc/api/public/v1/low-chip-metrics"


def load_token(env_file: Path = ENV_FILE) -> str:
    values: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    token = values.get("LOW_CHIP_SYNC_TOKEN", "")
    if not token:
        raise RuntimeError("LOW_CHIP_SYNC_TOKEN is missing")
    return token


def expected_by_date(history_dir: Path = HISTORY_DIR) -> dict[str, dict[str, dict[str, Any]]]:
    expected: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(history_dir.glob("????-??-??.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        date = str(payload.get("data_as_of") or path.stem).replace("-", "")
        records: dict[str, dict[str, Any]] = {}
        for full_code in payload.get("intersection") or []:
            rec = (payload.get("enrichments") or {}).get(full_code) or {}
            records[str(full_code).split(".")[0]] = {
                "industry_etfs": rec.get("industry_etfs") or [],
                "industry_etf_status": rec.get("industry_etf_status") or "unknown",
                "industry_etf_pool_count": rec.get("industry_etf_pool_count"),
            }
        expected[date] = records
    return expected


def _candidate_identity(rows: Any) -> list[tuple[str, str, str, str, str, str]] | None:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return [
        (
            str(row.get("code") or ""),
            str(row.get("name") or ""),
            str(row.get("theme") or ""),
            str(row.get("category") or ""),
            str(row.get("match_type") or ""),
            str(row.get("source_pool") or ""),
        )
        for row in rows
    ]


def validate_results(
    trade_date: str,
    expected: dict[str, dict[str, Any]],
    actual_rows: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    actual = {str(row.get("stock_code") or ""): row for row in actual_rows}
    if set(actual) != set(expected):
        errors.append(
            f"{trade_date}: member mismatch expected={sorted(expected)} actual={sorted(actual)}"
        )
    for code, want in expected.items():
        got = actual.get(code)
        if got is None:
            errors.append(f"{trade_date}/{code}: missing D1 row")
            continue
        for field in ("industry_etf_status", "industry_etf_pool_count"):
            if got.get(field) != want.get(field):
                errors.append(
                    f"{trade_date}/{code}: {field} expected={want.get(field)!r} got={got.get(field)!r}"
                )
        got_candidates = _candidate_identity(got.get("industry_etfs"))
        want_candidates = _candidate_identity(want.get("industry_etfs"))
        if got_candidates is None or want_candidates is None or got_candidates != want_candidates:
            errors.append(f"{trade_date}/{code}: industry_etfs mismatch")
    return errors


def fetch_date(trade_date: str, token: str) -> list[dict[str, Any]]:
    url = f"{ENDPOINT}?date={urllib.parse.quote(trade_date)}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "HermesLowChipIndustryEtfBackfill/1.0",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if payload.get("ok") is not True:
        raise RuntimeError(f"D1 query failed for {trade_date}: {payload}")
    return payload.get("results") or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    args = parser.parse_args()

    token = load_token(args.env_file)
    expected = expected_by_date()
    if not args.verify_only:
        env = os.environ.copy()
        env["LOW_CHIP_SYNC_TOKEN"] = token
        subprocess.run([sys.executable, str(SYNC_SCRIPT), "--history"], env=env, check=True)

    errors: list[str] = []
    total = 0
    for date, records in expected.items():
        actual = fetch_date(date, token)
        total += len(records)
        errors.extend(validate_results(date, records, actual))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"D1 industry ETF verification failed: {len(errors)} errors")
        return 1
    print(f"D1 industry ETF verification OK: {len(expected)} dates / {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
