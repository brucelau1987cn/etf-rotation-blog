#!/usr/bin/env python3
"""Query restricted-share unlock batches for the low-chip universe.

Uses Eastmoney datacenter-web RPT_LIFT_STAGE (this endpoint is NOT blocked,
unlike push2). Window: next 3 months from today (Asia/Shanghai).

Output: /tmp/low_chip_unlock.json in the shape enrich_low_chip_stocks.py expects:
  {"datas": [{"股票代码": "603407.SH", "变动日期": "20261111", "股份来源": "...", "占总股本比例": 0.0027}]}
Empty datas => no unlock risk within the window.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
OUT = Path("/tmp/low_chip_unlock.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "https://data.eastmoney.com/"
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"

LIFT_COLUMNS = (
    "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES_TYPE,TOTAL_RATIO,FREE_RATIO"
)


def fetch_unlocks(code: str) -> list[dict]:
    params = {
        "sortColumns": "FREE_DATE",
        "sortTypes": "1",
        "pageSize": "200",
        "pageNumber": "1",
        "reportName": "RPT_LIFT_STAGE",
        "filter": f'(SECURITY_CODE="{code}")',
        "columns": LIFT_COLUMNS,
        "source": "WEB",
        "client": "WEB",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode())
    return (payload.get("result") or {}).get("data") or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-days", type=int, default=92, help="unlock window in days (default 92 = ~3 months)")
    parser.add_argument("--output", default=str(OUT))
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = list(payload.get("intersection") or [])
    today = date.today()
    horizon = today + timedelta(days=args.window_days)

    rows: list[dict] = []
    for symbol in codes:
        code = symbol.split(".")[0]
        try:
            batches = fetch_unlocks(code)
        except Exception as exc:
            print(f"warn: {symbol} unlock query failed: {str(exc)[:120]}", file=__import__("sys").stderr)
            continue
        for batch in batches:
            free_date = str(batch.get("FREE_DATE") or "")[:10]
            if not free_date:
                continue
            if today.isoformat() <= free_date <= horizon.isoformat():
                rows.append({
                    "股票代码": symbol,
                    "变动日期": free_date.replace("-", ""),
                    "股份来源": str(batch.get("FREE_SHARES_TYPE") or "限售股"),
                    "占总股本比例": round(float(batch.get("TOTAL_RATIO") or 0), 4),
                    "占流通股比例": round(float(batch.get("FREE_RATIO") or 0), 4),
                })

    result = {"datas": rows, "window_start": today.isoformat(), "window_end": horizon.isoformat()}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"status": "ok", "window": [today.isoformat(), horizon.isoformat()],
                      "unlock_rows": len(rows), "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
