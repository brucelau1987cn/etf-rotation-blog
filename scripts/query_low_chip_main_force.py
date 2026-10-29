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
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
OUT = Path("/tmp/low_chip_main_force.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# The endpoint returns one row per (stock, trade date), sorted by TRADE_DATE desc.
#
# Scale notes (measured 2026-10-29 on a 442-stock pool):
#   * pageSize=100 returned only the 100 newest rows — 342 stocks were dropped
#     silently, so 机构参与度 was missing for most of the published page.
#   * The URL embeds every requested code, so the request has to be split into
#     chunks before it grows past what the endpoint accepts.
# So: chunk the codes, then paginate inside each chunk until every requested
# code has a row or the endpoint runs out of pages.
PAGE_SIZE = 500
CODE_CHUNK = 200
MAX_PAGES_PER_CHUNK = 40


def _request(codes: list[str], page: int) -> tuple[list[dict], int]:
    quoted = ",".join(f'%22{c}%22' for c in codes)  # pre-encoded double quotes
    filt = f"(SECURITY_CODE in ({quoted}))"
    params = (
        "reportName=RPT_DMSK_TS_STOCKEVALUATE"
        f"&filter={filt}&columns=ALL&source=WEB&client=WEB"
        f"&sortColumns=TRADE_DATE&sortTypes=-1&pageSize={PAGE_SIZE}&pageNumber={page}"
    )
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?{params}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    result = (resp.json().get("result") or {})
    return result.get("data") or [], int(result.get("pages") or 0)


def fetch(codes: list[str]) -> list[dict]:
    """Fetch latest main-force row per stock."""
    if not codes:
        return []
    seen: dict[str, dict] = {}
    for offset in range(0, len(codes), CODE_CHUNK):
        chunk = codes[offset:offset + CODE_CHUNK]
        wanted = set(chunk)
        page = 1
        pages = 1
        while page <= min(pages, MAX_PAGES_PER_CHUNK):
            rows, pages = _request(chunk, page)
            if not rows:
                break
            for r in rows:
                code = r.get("SECURITY_CODE")
                # keep the latest (first after sort desc) per code
                if code and code not in seen:
                    seen[code] = r
            if wanted.issubset(seen.keys()):
                break
            page += 1
    return list(seen.values())


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = [c.split(".")[0] for c in (payload.get("intersection") or [])]
    rows = fetch(codes)
    OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    covered = {str(r.get("SECURITY_CODE") or "") for r in rows}
    missing = [c for c in codes if c not in covered]
    print(f"main force rows: {len(rows)} -> {OUT}", flush=True)
    if missing:
        # Fail loudly instead of letting attach_low_chip_shareholder_metrics.py
        # silently publish a partially-covered 机构参与度 column.
        raise SystemExit(
            f"main force coverage incomplete: {len(rows)}/{len(codes)} stocks; "
            f"missing {len(missing)} e.g. {missing[:10]}"
        )
    for r in rows:
        org = r.get("ORG_PARTICIPATE")
        print(f"  {r.get('SECURITY_CODE')} {round(org*100,2) if org is not None else '-'}% {r.get('PARTICIPATE_TYPE_CN')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())