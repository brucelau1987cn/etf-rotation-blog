#!/usr/bin/env python3
"""Backfill ETF holdings for a single low-chip history snapshot (yesterday).

Designed for the cheap-iWenCai-quota case: only queries codes that exist in
ONE specific snapshot file (default = latest calendar day before today),
never touches the rest of history. ~30 codes × 1 iWenCai call = ~80s.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "public/data/a-low-chip-stocks.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
CN = ZoneInfo("Asia/Shanghai")

spec = importlib.util.spec_from_file_location("attach_low_chip_etf_holdings", SCRIPTS / "attach_low_chip_etf_holdings.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def _latest_history_date() -> str:
    """Returns the latest calendar date available in low-chip-history/."""
    files = sorted(HISTORY_DIR.glob("*.json"))
    return files[-1].stem if files else ""


def _default_yesterday() -> str:
    """Default = yesterday in Asia/Shanghai (interpreted as the most recent
    trading day). Falls back to latest history file if no calendar match.
    """
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    return yesterday


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=_default_yesterday(),
                        help="YYYY-MM-DD to backfill (default: yesterday in CN tz)")
    parser.add_argument("--current-only", action="store_true",
                        help="Only attach the current data file (skip history lookup)")
    parser.add_argument("--reuse-current", action="store_true",
                        help="Prefer codes already present in current a-low-chip-stocks.json "
                             "(avoid hitting iWenCai for codes we already have data on)")
    args = parser.parse_args()

    if args.current_only:
        targets = [DATA]
    else:
        target = HISTORY_DIR / f"{args.date}.json"
        if not target.exists():
            # Fallback: latest history file (most recent snapshot date)
            fallback = _latest_history_date()
            if not fallback:
                print(f"FATAL: no history files under {HISTORY_DIR}", file=sys.stderr)
                return 1
            print(f"WARNING: {target.name} not found, falling back to {fallback}.json")
            target = HISTORY_DIR / f"{fallback}.json"
        targets = [target]

    # Pre-load current data so --reuse-current avoids iWenCai for known codes
    cur_lookup: dict[str, dict] = {}
    if args.reuse_current and DATA.exists():
        cur = json.loads(DATA.read_text(encoding="utf-8"))
        for code, rec in (cur.get("enrichments") or {}).items():
            if rec.get("etf_holdings"):
                cur_lookup[code] = rec

    total_queries = 0
    for path in targets:
        d = json.loads(path.read_text(encoding="utf-8"))
        symbols = sorted(set(d.get("intersection") or []) | set(d.get("enrichments", {}).keys()))
        enrichments = d.setdefault("enrichments", {})
        print(f"=== {path.name}: {len(symbols)} codes (cache hits: "
              f"{sum(1 for c in symbols if c in cur_lookup)}) ===")
        if not symbols:
            print("  (empty, skipping)")
            continue
        for i, code in enumerate(symbols, 1):
            cached = cur_lookup.get(code)
            if cached and cached.get("etf_holdings"):
                holdings = cached["etf_holdings"]
                cat = cached.get("etf_top_category")
                rec = enrichments.setdefault(code, {})
                rec["etf_holdings"] = holdings
                rec["etf_top_category"] = cat
                print(f"  [{i}/{len(symbols)}] {code}: cache hit ({len(holdings)} ETFs, {cat})")
                continue
            holdings, cat, raw_n = helper.attach_for_one(code)
            rec = enrichments.setdefault(code, {})
            rec["etf_holdings"] = holdings
            rec["etf_top_category"] = cat
            total_queries += 1
            print(f"  [{i}/{len(symbols)}] {code}: {raw_n} raw, top={holdings[0]['name'] if holdings else '—'} ({cat})")
            time.sleep(helper.DELAY_BETWEEN)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        print(f"  → wrote {path.name} (iWenCai queries this file: "
              f"{sum(1 for c in symbols if c not in cur_lookup)})")

    print(f"\nTotal iWenCai queries: {total_queries}")
    return 0


if __name__ == "__main__":
    sys.exit(main())