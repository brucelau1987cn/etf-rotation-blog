#!/usr/bin/env python3
"""Backfill chip_focus and report_period into historical low-chip snapshots.

Queries Eastmoney HSF10 for every unique stock code across all history
snapshots, then updates each snapshot's shareholder_metrics with
chip_focus, report_period, and other eastmoney fields.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

HISTORY_DIR = Path("public/data/low-chip-history")
INDEX_FILE = Path("public/data/low-chip-history-index.json")
DATA_FILE = Path("public/data/a-low-chip-stocks.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _exchange(code: str) -> str:
    bare = code.split(".")[0]
    if bare.startswith("6") or bare.startswith("688"):
        return "SH"
    return "SZ"


def fetch_em_holder(code: str) -> dict | None:
    """Fetch shareholder data from Eastmoney HSF10 for a single stock."""
    bare = code.split(".")[0]
    exch = _exchange(code)
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={exch}{bare}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  {code}: fetch failed: {exc}", flush=True)
        return None
    gdrs = data.get("gdrs") or []
    if not gdrs:
        print(f"  {code}: no gdrs", flush=True)
        return None
    row = gdrs[0]
    holder_total = row.get("HOLDER_TOTAL_NUM")
    total_ratio = row.get("TOTAL_NUM_RATIO")
    focus = row.get("HOLD_FOCUS")
    freehold_ratio = row.get("FREEHOLD_RATIO_TOTAL")
    avg_free = row.get("AVG_FREE_SHARES")
    end_date = str(row.get("END_DATE", ""))[:10] if row.get("END_DATE") else None
    price = row.get("PRICE")
    if holder_total is not None and total_ratio is not None and total_ratio != 0:
        prev_holder = round(holder_total / (1 + total_ratio / 100))
    else:
        prev_holder = None
    return {
        "shareholder_count": holder_total,
        "previous_shareholder_count": prev_holder,
        "shareholder_change_pct": total_ratio,
        "average_holding": avg_free,
        "top10_float_ratio": freehold_ratio,
        "chip_focus": focus,
        "report_period": end_date,
        "price": price,
    }


def fetch_em_main_force(codes: list[str]) -> dict[str, dict]:
    """Fetch main-force (千股千评 机构参与度) from Eastmoney datacenter."""
    if not codes:
        return {}
    bare_codes = [c.split(".")[0] for c in codes]
    quoted = ",".join(f'%22{c}%22' for c in bare_codes)
    filt = f"(SECURITY_CODE in ({quoted}))"
    params = (
        "reportName=RPT_DMSK_TS_STOCKEVALUATE"
        f"&filter={filt}&columns=ALL&source=WEB&client=WEB"
        "&sortColumns=TRADE_DATE&sortTypes=-1&pageSize=100"
    )
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?{params}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        rows = ((resp.json() or {}).get("result") or {}).get("data") or []
    except Exception as exc:
        print(f"  main-force fetch failed: {exc}", flush=True)
        return {}
    seen: dict[str, dict] = {}
    for r in rows:
        c = r.get("SECURITY_CODE")
        if c and c not in seen:
            seen[c] = {
                "main_force": round(r["ORG_PARTICIPATE"] * 100, 2) if r.get("ORG_PARTICIPATE") is not None else None,
                "main_force_label": r.get("PARTICIPATE_TYPE_CN"),
            }
    return seen


def main() -> int:
    # collect all unique codes across all history snapshots
    snapshots = sorted(HISTORY_DIR.glob("????-??-??.json"))
    all_codes: set[str] = set()
    for snap in snapshots:
        d = json.loads(snap.read_text(encoding="utf-8"))
        all_codes.update(d.get("intersection") or [])
    # also include the current data file
    if DATA_FILE.exists():
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        all_codes.update(d.get("intersection") or [])
    codes = sorted(all_codes)
    print(f"unique codes across all snapshots: {len(codes)}", flush=True)

    # fetch from eastmoney
    em_data: dict[str, dict] = {}
    for code in codes:
        print(f"fetching {code}...", flush=True)
        result = fetch_em_holder(code)
        if result:
            em_data[code] = result

    print(f"fetched: {len(em_data)}/{len(codes)}", flush=True)

    # fetch main-force from eastmoney datacenter
    print("fetching main-force from datacenter...", flush=True)
    mf_data = fetch_em_main_force(codes)

    # update each snapshot
    updated = 0
    for snap in snapshots:
        d = json.loads(snap.read_text(encoding="utf-8"))
        changed = False
        for code in d.get("intersection") or []:
            if code not in em_data:
                continue
            em = dict(em_data[code])  # copy
            # merge main-force
            bare = code.split(".")[0]
            mf = mf_data.get(bare, {})
            em["main_force"] = mf.get("main_force")
            em["main_force_label"] = mf.get("main_force_label")
            if code not in d.get("enrichments", {}):
                d.setdefault("enrichments", {})[code] = {}
            d["enrichments"][code]["shareholder_metrics"] = em
            changed = True
        if changed:
            snap.write_text(
                json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            updated += 1
            print(f"  updated {snap.name}", flush=True)

    # also update the current data file (if it exists and has intersection)
    if DATA_FILE.exists():
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        changed = False
        for code in d.get("intersection") or []:
            if code in em_data:
                em = dict(em_data[code])
                bare = code.split(".")[0]
                mf = mf_data.get(bare, {})
                em["main_force"] = mf.get("main_force")
                em["main_force_label"] = mf.get("main_force_label")
                d["enrichments"][code]["shareholder_metrics"] = em
                changed = True
        if changed:
            DATA_FILE.write_text(
                json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            print(f"  updated current data file", flush=True)

    # NOTE: index is intentionally NOT rebuilt here — it only carries
    # dates/counts (no shareholder data). Rebuild via archive_low_chip_snapshot.py
    # if the index itself needs refreshing.
    print(f"updated {updated}/{len(snapshots)} snapshots + current data file", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())