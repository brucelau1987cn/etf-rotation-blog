#!/usr/bin/env python3
"""Query Eastmoney 千股千评 main-force control (机构参与度) for low-chip stocks.

Source: datacenter-web RPT_DMSK_TS_STOCKEVALUATE
          ORG_PARTICIPATE  = 机构参与度 (e.g. 0.2584 → 25.84%)
          PARTICIPATE_TYPE_CN = 控盘等级 (中度控盘 / 轻度控盘 / ...)
          PRIME_COST / PRIME_COST_20DAYS / PRIME_COST_60DAYS = 主力成本
Writes /tmp/low_chip_main_force.json (one row per stock, latest trade date).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
OUT = Path("/tmp/low_chip_main_force.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}


def fetch(codes: list[str]) -> list[dict]:
    """Fetch latest main-force row per stock."""
    if not codes:
        return []
    quoted = ",".join(f'"{c}"' for c in codes)
    filt = f"(SECURITY_CODE in ({urllib.parse.quote(quoted)}))"
    params = (
        "reportName=RPT_DMSK_TS_STOCKEVALUATE"
        f"&filter={filt}&columns=ALL&source=WEB&client=WEB"
        "&sortColumns=TRADE_DATE&sortTypes=-1&pageSize=100"
    )
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    rows = (data.get("result") or {}).get("data") or []
    # keep the latest (first after sort desc) per code
    seen: dict[str, dict] = {}
    for r in rows:
        code = r.get("SECURITY_CODE")
        if code and code not in seen:
            seen[code] = r
    return list(seen.values())


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = [c.split(".")[0] for c in (payload.get("intersection") or [])]
    rows = fetch(codes)
    OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"main force rows: {len(rows)} -> {OUT}", flush=True)
    for r in rows:
        org = r.get("ORG_PARTICIPATE")
        print(f"  {r.get('SECURITY_CODE')} {round(org*100,2) if org is not None else '-'}% {r.get('PARTICIPATE_TYPE_CN')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())