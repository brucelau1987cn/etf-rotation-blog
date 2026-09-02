#!/usr/bin/env python3
"""Backfill ETF holdings for low-chip history snapshots + current data file.

Reads each `low-chip-history/{YYYY-MM-DD}.json` + current `a-low-chip-stocks.json`,
collects every unique stock code across all of them, queries iWenCai once per code,
rewrites `enrichments[code].etf_holdings` (top N) + `etf_top_category` in each snapshot.

Faster than per-day attach — only one iWenCai call per *unique* code, not per
(snapshot × stock). For 32 stocks × 22 days ≈ 700 attach calls would be ~30 min;
this is 32 calls ≈ 90s.

Uses iWenCai via the same helper as `attach_low_chip_etf_holdings.py`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "public/data/a-low-chip-stocks.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
INDEX = ROOT / "public/data/low-chip-history-index.json"

# Reuse the helper module's parse functions without re-implementing
spec = importlib.util.spec_from_file_location("attach_low_chip_etf_holdings", SCRIPTS / "attach_low_chip_etf_holdings.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

DELAY_BETWEEN = 0.30


def collect_unique_codes() -> dict[str, set[str]]:
    """Returns {file_label: set(codes)} — never miss any stock that ever appeared."""
    out: dict[str, set[str]] = {"__current__": set(), "__history__": set()}
    cur = json.loads(DATA.read_text(encoding="utf-8"))
    cur_codes = set(cur.get("intersection") or [])
    cur_codes.update(cur.get("enrichments", {}).keys())
    out["__current__"] = cur_codes
    for p in sorted(HISTORY_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        codes = set(d.get("intersection") or [])
        codes.update(d.get("enrichments", {}).keys())
        out[p.name] = codes
        out["__history__"].update(codes)
    return out


def main() -> int:
    by_file = collect_unique_codes()
    # Union — query each code exactly once
    all_codes = set()
    for codes in by_file.values():
        all_codes.update(codes)
    print(f"unique codes across all snapshots + current: {len(all_codes)}")
    if not all_codes:
        return 0

    # iWenCai once per code → store by bare code
    holdings_map: dict[str, list[dict]] = {}
    category_map: dict[str, str | None] = {}
    raw_counts: dict[str, int] = {}
    for i, code in enumerate(sorted(all_codes), 1):
        holdings, cat, raw_n = helper.attach_for_one(code)
        holdings_map[code] = holdings
        category_map[code] = cat
        raw_counts[code] = raw_n
        print(f"[{i}/{len(all_codes)}] {code}: {raw_n} raw rows, top={holdings[0]['name'] if holdings else '—'} ({cat})")
        time.sleep(DELAY_BETWEEN)

    # Write back current data file
    cur = json.loads(DATA.read_text(encoding="utf-8"))
    enrichments = cur.setdefault("enrichments", {})
    for code in cur.get("intersection") or []:
        rec = enrichments.setdefault(code, {})
        rec["etf_holdings"] = holdings_map.get(code, [])
        rec["etf_top_category"] = category_map.get(code)
    tmp = DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DATA)
    print(f"current file updated: {DATA.name}")

    # Write back each history snapshot
    snapshots_updated = 0
    for p in sorted(HISTORY_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        enrichments_snap = d.setdefault("enrichments", {})
        changed = False
        for code in d.get("intersection") or []:
            rec = enrichments_snap.setdefault(code, {})
            new_h = holdings_map.get(code, [])
            new_c = category_map.get(code)
            if rec.get("etf_holdings") != new_h or rec.get("etf_top_category") != new_c:
                rec["etf_holdings"] = new_h
                rec["etf_top_category"] = new_c
                changed = True
        if changed:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(p)
            snapshots_updated += 1
    print(f"history snapshots updated: {snapshots_updated}/{len(list(HISTORY_DIR.glob('*.json')))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())