#!/usr/bin/env python3
"""Attach ETF-holding data per low-chip stock via iWenCai.

For each symbol in `intersection`, query `{code} 持有ETF,基金类型包含ETF` and
attach a normalized list to `enrichments[code].etf_holdings` (top N by weight)
plus a single `etf_top_category` (the most common etf_category_l2 — that is
the "板块 ETF" the stock belongs to).

Failure-soft per existing contract: any HTTP error or non-JSON row leaves
`etf_holdings=[]` + `etf_top_category=null`. Never raises — orchestrator treats
missing fields as `UNAVAILABLE`.

Outputs are atomic JSON write with `separators=(",", ":")` + `ensure_ascii=False`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

TOP_N_HOLDINGS = 5      # keep top-N ETFs by weight (smaller list for the page)
DELAY_BETWEEN = 0.30    # seconds between iWenCai calls (rate-limit)
IWC_BIN = "/root/.hermes/scripts/iwencai-market-query"
IWC_TIMEOUT = 30        # seconds per query
IWC_QUERY = "{code} 持有ETF,基金类型包含ETF"  # also exposes etf_category_l1/l2

# iWenCai returns float-as-JSON with .000000… drift; trim to 4 decimals
def _trim(v: Any, ndigits: int = 4) -> Any:
    if isinstance(v, float):
        return round(v, ndigits)
    return v


def iwc_query(bare_code: str) -> list[dict]:
    """Run iWenCai query and return the `datas` list (may be empty)."""
    q = IWC_QUERY.format(code=bare_code)
    try:
        r = subprocess.run(
            [IWC_BIN, "-q", q, "--page", "1", "--limit", "100", "--timeout", str(IWC_TIMEOUT)],
            capture_output=True, text=True, check=False, timeout=IWC_TIMEOUT + 10,
        )
    except subprocess.TimeoutExpired:
        print(f"[{bare_code}] iWenCai timeout", file=sys.stderr)
        return []
    if r.returncode != 0:
        print(f"[{bare_code}] iWenCai rc={r.returncode}: {r.stderr[:120]}", file=sys.stderr)
        return []
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print(f"[{bare_code}] iWenCai non-JSON: {e}, head={r.stdout[:80]}", file=sys.stderr)
        return []
    return d.get("datas") or []


def parse_holdings(rows: list[dict]) -> list[dict]:
    """Normalize iWenCai rows to our schema, sorted by weight desc."""
    out = []
    for row in rows:
        # Skip non-ETF rows (e.g. 场外主动 / LOF — they have no weight)
        cat = row.get("etf类型一级分类") or ""
        if "ETF" not in cat:
            continue
        weight = row.get("持仓市值占基金资产净值比")
        rank = row.get("排名")
        if weight is None or rank is None:
            continue
        out.append({
            "code": row.get("基金代码"),
            "name": row.get("基金简称"),
            "full_name": row.get("基金扩位简称"),
            "weight_pct": _trim(weight),
            "rank": int(rank) if rank == int(rank) else rank,
            "holding_value": _trim(row.get("持仓市值")),
            "holding_qty": _trim(row.get("持仓数量")),
            "is_heavy": bool(row.get("是否重仓")),
            "etf_category_l1": cat,
            "etf_category_l2": row.get("etf类型二级分类"),
            "fund_manager": (row.get("现任基金经理姓名") or [None])[0] if isinstance(row.get("现任基金经理姓名"), list) else row.get("现任基金经理姓名"),
        })
    out.sort(key=lambda x: (-float(x["weight_pct"] or 0), x["rank"] or 0))
    return out


def top_category(rows: list[dict]) -> str | None:
    """Most common etf_category_l2 among all ETF rows (NOT just top-N)."""
    cats = [r.get("etf类型二级分类") for r in rows if r.get("etf类型二级分类")]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]


def attach_for_one(code: str) -> tuple[list[dict], str | None, int]:
    """Returns (top_N_holdings, top_category, raw_row_count)."""
    bare = code.split(".")[0]
    rows = iwc_query(bare)
    if not rows:
        return [], None, 0
    holdings = parse_holdings(rows)
    cat = top_category(rows)
    return holdings[:TOP_N_HOLDINGS], cat, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DATA))
    parser.add_argument("--delay", type=float, default=DELAY_BETWEEN)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"FATAL: input file not found: {src}", file=sys.stderr)
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    symbols = payload.get("intersection") or []
    enrichments = payload.setdefault("enrichments", {})
    if not symbols:
        print("intersection is empty, nothing to do")
        return 0

    success = 0
    empty = 0
    failed_codes = []
    for i, code in enumerate(symbols, 1):
        rec = enrichments.setdefault(code, {})
        rec.setdefault("etf_holdings", None)
        rec.setdefault("etf_top_category", None)

        holdings, cat, raw_n = attach_for_one(code)
        if raw_n == 0:
            empty += 1
            print(f"[{i}/{len(symbols)}] {code}: 0 rows (ETF holdings unavailable)")
        else:
            success += 1
            print(f"[{i}/{len(symbols)}] {code}: {raw_n} ETFs, top_weight={holdings[0]['weight_pct'] if holdings else '—'}, cat={cat}")

        # Atomic per-record write so partial progress survives crashes
        if not args.dry_run:
            rec["etf_holdings"] = holdings
            rec["etf_top_category"] = cat

        time.sleep(args.delay)

    if not args.dry_run:
        # Atomic write with compact JSON (matches existing contract)
        tmp = src.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(src)
    print(f"\nETF holdings attach: {success} ok, {empty} empty/0-row, total {len(symbols)}")
    if failed_codes:
        print(f"failed codes: {failed_codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())