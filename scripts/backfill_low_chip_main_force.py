#!/usr/bin/env python3
"""Backfill main_force (ORG_PARTICIPATE) into a low-chip history snapshot."""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

SNAPSHOT = Path(sys.argv[1] if len(sys.argv) > 1 else "public/data/low-chip-history/2026-07-31.json")


def fetch(code: str) -> dict | None:
    filt = f'(SECURITY_CODE="{code}")'
    params = (
        "reportName=RPT_DMSK_TS_STOCKEVALUATE"
        f"&filter={urllib.parse.quote(filt)}"
        "&columns=ALL&source=WEB&client=WEB&sortColumns=TRADE_DATE&sortTypes=-1&pageSize=1"
    )
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?{params}"
    r = subprocess.run(
        ["curl", "-s", "-H", "User-Agent: Mozilla/5.0", "-H", "Referer: https://data.eastmoney.com/", url],
        capture_output=True, text=True, timeout=20,
    )
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    rows = (d.get("result") or {}).get("data") or []
    return rows[0] if rows else None


def main() -> int:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    codes = [c.split(".")[0] for c in snap.get("intersection", [])]
    updated = 0
    missing = []
    for code in codes:
        mf = fetch(code)
        enr = snap.get("enrichments", {}).get(code + ".SZ") or snap.get("enrichments", {}).get(code + ".SH")
        if not mf:
            missing.append(code)
            continue
        if not enr:
            continue
        sm = enr.setdefault("shareholder_metrics", {})
        org = mf.get("ORG_PARTICIPATE")
        if org is not None:
            sm["main_force"] = round(org * 100, 2)
            sm["main_force_label"] = mf.get("PARTICIPATE_TYPE_CN")
            updated += 1
        print(f"  {code}: {sm.get('main_force')}% {sm.get('main_force_label')}")
    snap["_main_force_source"] = "eastmoney RPT_DMSK_TS_STOCKEVALUATE (ORG_PARTICIPATE)"
    snap["_main_force_updated"] = "2026-08-09"
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"updated {updated}/{len(codes)}; missing={missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())