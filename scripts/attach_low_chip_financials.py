#!/usr/bin/env python3
"""Attach Fuyao financials within a bounded budget; atomically save complete coverage."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "public/data/a-low-chip-stocks.json"
CN = ZoneInfo("Asia/Shanghai")
from audit_low_chip_fuyao import FuyaoClient, load_api_key  # noqa: E402

REPORT_SUFFIX = {"1": "0331", "2": "0630", "3": "0930", "4": "1231"}
FINANCIAL_FIELDS = ("roe", "net_margin", "gross_margin", "debt_ratio", "cash_profit_ratio")


def _latest_report(now: dt.datetime) -> str:
    month, year = now.month, now.year
    if month <= 4:
        return f"{year - 1}-4"
    if month <= 8:
        return f"{year}-1"
    if month <= 10:
        return f"{year}-2"
    return f"{year}-3"


def _prev_report(report: str) -> str:
    year, quarter = report.split("-")
    q = int(quarter)
    return f"{int(year) - 1}-4" if q == 1 else f"{year}-{q - 1}"


def _report_to_period(report: str) -> str:
    year, quarter = report.split("-")
    return f"{year}{REPORT_SUFFIX[quarter]}"


def attach_financials(payload, client, target, *, budget_seconds=180.0, monotonic=time.monotonic):
    deadline = monotonic() + max(0.0, budget_seconds)
    client.deadline = deadline
    codes = payload["intersection"]
    report_period = None
    covered = 0
    for code in codes:
        enrichment = payload.setdefault("enrichments", {}).setdefault(code, {})
        existing = enrichment.get("financials") or {}
        fin = {}
        report = target
        reason = "no_data"
        for _ in range(3):
            if monotonic() >= deadline:
                reason = "budget_exhausted"
                break
            try:
                fin = client.financials(code, report)
                if any(fin.get(field) is not None for field in FINANCIAL_FIELDS):
                    break
                fin = {}
            except (RuntimeError, OSError, ValueError) as exc:
                reason = type(exc).__name__
            report = _prev_report(report)
        if fin:
            period = _report_to_period(report)
            enrichment["financials"] = {
                "status": "AVAILABLE", "report_period": period,
                **{field: fin.get(field) for field in FINANCIAL_FIELDS},
            }
        elif any(existing.get(field) is not None for field in FINANCIAL_FIELDS):
            enrichment["financials"] = {**existing, "status": "AVAILABLE", "refresh_status": reason}
        else:
            enrichment["financials"] = {
                "status": "UNAVAILABLE", "reason": reason, "report_period": None,
                **{field: None for field in FINANCIAL_FIELDS},
            }
        current = enrichment["financials"]
        if current["status"] == "AVAILABLE":
            covered += 1
            report_period = report_period or current.get("report_period")
    payload["financial_coverage"] = {
        "covered": covered, "requested": len(codes), "failed": len(codes) - covered,
        "budget_seconds": budget_seconds, "budget_exhausted": monotonic() >= deadline,
    }
    payload["financial_filters"] = {
        "report_period": report_period,
        "roe_min": 15, "net_margin_min": 15, "cash_profit_ratio_min": 20,
        "gross_margin_min": 15, "debt_ratio_max": 30,
        "labels": {
            "roe": "ROE ≥ 15%", "net_margin": "净利率 ≥ 15%",
            "cash_profit_ratio": "现金流/净利润 ≥ 20%", "gross_margin": "毛利率 ≥ 15%",
            "debt_ratio": "负债率 ≤ 30%",
        },
    }
    return payload


def _write_atomic(path, payload):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=None)
    parser.add_argument("--input", default=None, help="兼容旧参数")
    parser.add_argument("--budget-seconds", type=float, default=180.0)
    parser.add_argument("--request-timeout", type=float, default=5.0)
    args = parser.parse_args()
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    target = args.report or _latest_report(dt.datetime.now(CN))
    try:
        client = FuyaoClient(load_api_key(), qps=2.0, request_timeout=args.request_timeout)
    except RuntimeError:
        client = None
    if client is None:
        attach_financials(payload, type("Unavailable", (), {})(), target, budget_seconds=0)
    else:
        attach_financials(payload, client, target, budget_seconds=args.budget_seconds)
    _write_atomic(DATA, payload)
    print(json.dumps({"status": "ok", **payload["financial_coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
