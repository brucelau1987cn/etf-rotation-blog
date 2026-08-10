#!/usr/bin/env python3
"""Fetch profile, individual, and quality shareholder queries for low-chip stocks."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

PROFILE_BATCH_SIZE = 5  # iWenCai batch size per /tmp/lc_p{n}.json

QUALITY_QUERY = " 前十大流通股东名称包含全国社保基金或基本养老保险基金或国家集成电路产业投资基金或国新投资或深圳市创新投资集团或科威特政府投资局或澳门金融管理局"


def iwc(query, page=1, limit=50, timeout=90):
    r = subprocess.run(
        ["/root/.hermes/scripts/iwencai-market-query", "-q", query, "--page", str(page),
         "--limit", str(limit), "--timeout", str(timeout)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"iWenCai error: {r.stderr[:200]}")
    return json.loads(r.stdout)


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = list(payload.get("intersection") or [])
    bare_codes = [c.split(".")[0] for c in codes]
    print(f"intersection: {codes}", flush=True)

    # /tmp/lc_p{n}.json — per-stock profile query (industry, top10, shareholders)
    # 5 codes per batch
    batches = [bare_codes[i:i + PROFILE_BATCH_SIZE] for i in range(0, len(bare_codes), PROFILE_BATCH_SIZE)]
    if not batches:
        batches = [[]]
    for idx, batch in enumerate(batches, start=1):
        if not batch:
            continue
        q = "、".join(batch) + " 所属申万行业、最新报告期前十大流通股东名称、最新报告期流通股东名称、流通股东类型、持股数量、持股比例、流通股东持股变动类型、公告日期、所属概念"
        d = iwc(q)
        out = Path(f"/tmp/lc_p{idx}.json")
        out.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"  lc_p{idx}: rows={len(d.get('datas') or [])}", flush=True)

    # /tmp/low_chip_individual.json — per-stock current shareholding row with industry/concept
    rows = []
    for code in codes:
        q = f"{code.split('.')[0]} 最新价、最新涨跌幅、所属申万行业、所属概念、前十大流通股东、第一大流通股东名称、持股数量、持股市值、占总股本比、排名、持股变动类型、公告日期、上市地点、所属同花顺行业、上市板块"
        d = iwc(q)
        for r in d.get("datas") or []:
            if str(r.get("股票代码", "")).upper().startswith(code.split(".")[0]):
                rows.append(r)
                break
    Path("/tmp/low_chip_individual.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"  individual rows: {len(rows)}", flush=True)

    # /tmp/low_chip_quality.json — quality shareholders check
    q = "、".join(bare_codes) + QUALITY_QUERY
    d = iwc(q, limit=max(20, len(bare_codes) * 3))
    Path("/tmp/low_chip_quality.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"  quality rows: {len(d.get('datas') or [])}", flush=True)

    # /tmp/low_chip_financial_test_annual.json — annual financials (20251231)
    q = "、".join(bare_codes) + " 净资产收益率[20251231]、加权净资产收益率[20251231]、销售净利率[20251231]、经营活动产生的现金流量净额[20251231]、归属于母公司所有者的净利润[20251231]、销售毛利率[20251231]、资产负债率[20251231]"
    d = iwc(q, limit=max(20, len(bare_codes) * 3))
    Path("/tmp/low_chip_financial_test_annual.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"  financial rows: {len(d.get('datas') or [])}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())