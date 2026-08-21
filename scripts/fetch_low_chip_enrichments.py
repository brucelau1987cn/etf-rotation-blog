#!/usr/bin/env python3
"""Fetch profile, individual, and quality shareholder queries for low-chip stocks."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

PROFILE_BATCH_SIZE = 5  # iWenCai batch size per /tmp/lc_p{n}.json
SHAREHOLDER_PERIOD_RE = re.compile(r"^前十大流通股东名称(?:\(报告期\))?\[(\d{8})\]$")

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


def has_top10_names(row: dict | None) -> bool:
    return bool(row and any(
        key.startswith("前十大流通股东名称") and value
        for key, value in row.items()
    ))


def report_period_from_rows(rows: list[dict]) -> str:
    periods = []
    for row in rows:
        for key in row:
            match = SHAREHOLDER_PERIOD_RE.match(key)
            if match:
                periods.append(match.group(1))
            key_period = re.search(r"(?:报告期|截止日期)\[(\d{8})\]", key)
            if key_period:
                periods.append(key_period.group(1))
        for key in ("截止日期", "报告期"):
            value = str(row.get(key) or "")
            digits = re.sub(r"\D", "", value)
            if len(digits) >= 8:
                periods.append(digits[:8])
    return max(periods) if periods else ""


def aggregate_top10_detail(code: str, rows: list[dict], fallback_period: str = "") -> dict | None:
    """Build one current top-10 evidence row from iWenCai detail rows.

    The detail engine currently returns one holder per row under `名称`, while
    older responses used `流通股东名称`. Historical exits marked `新出` lack
    current rank/announcement evidence and are deliberately excluded.
    """
    bare = code.split(".")[0]
    selected = []
    for row in rows:
        if str(row.get("股票代码") or "").upper().split(".")[0] != bare:
            continue
        if row.get("排名") is None or not (row.get("公告日期") or any(str(k).startswith("公告日期[") for k in row)):
            continue
        name = str(row.get("流通股东名称") or row.get("股东名称") or row.get("名称") or "").strip()
        if name and name not in selected:
            selected.append(name)
    period = report_period_from_rows(rows) or fallback_period
    if not selected or not period:
        return None
    return {"股票代码": code, f"前十大流通股东名称(报告期)[{period}]": ", ".join(selected)}


def select_latest_top10_report_row(code: str, rows: list[dict]) -> dict | None:
    """Select the latest explicitly dated consolidated top-10 holder field."""
    bare = code.split(".")[0]
    candidates: list[tuple[str, str, object]] = []
    for row in rows:
        if str(row.get("股票代码") or "").upper().split(".")[0] != bare:
            continue
        for key, value in row.items():
            match = SHAREHOLDER_PERIOD_RE.match(key)
            if match and value:
                candidates.append((match.group(1), key, value))
    if not candidates:
        return None
    _, key, value = max(candidates, key=lambda item: item[0])
    return {"股票代码": code, key: value}


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
        q = f"{code.split('.')[0]} 最新价、最新涨跌幅、所属申万行业、所属概念、最新完整报告期十大流通股东明细、前十大流通股东名称、股东名称、股东类型、持股比例、排名、持股变动类型、公告日期、上市地点、所属同花顺行业、上市板块"
        d = iwc(q)
        matched = None
        for r in d.get("datas") or []:
            row_code = str(r.get("股票代码", "")).upper().split(".")[0]
            if row_code == code.split(".")[0]:
                matched = r
                break
        # Batch-style shareholder fields can return an incomplete row set.
        # Retry the industry/quote identity query per symbol before fail-closed validation.
        if matched is None or not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
            fallback = iwc(
                f"{code.split('.')[0]} 所属申万行业、所属同花顺行业、股票简称、最新价、最新涨跌幅",
                limit=10,
            )
            fallback_rows = fallback.get("datas") or []
            for r in fallback_rows:
                row_code = str(r.get("股票代码", "")).upper().split(".")[0]
                if row_code == code.split(".")[0]:
                    if matched is None:
                        matched = r
                    elif not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
                        matched["所属申万行业"] = r.get("所属申万行业")
                        matched["所属同花顺行业"] = r.get("所属同花顺行业")
                    break
            if matched is None or not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
                raise SystemExit(
                    f"missing industry after per-symbol fallback: {code}; "
                    f"returned={len(fallback_rows)}"
                )
        if not has_top10_names(matched):
            holder_detail = iwc(
                f"{code.split('.')[0]} 最新完整报告期十大流通股东明细、前十大流通股东名称、公告日期",
                limit=30,
            )
            detail_rows = holder_detail.get("datas") or []
            detail = next((
                r for r in detail_rows
                if str(r.get("股票代码") or "").upper().split(".")[0] == code.split(".")[0]
                and has_top10_names(r)
            ), None)
            if detail is None:
                detail = aggregate_top10_detail(
                    code, detail_rows, report_period_from_rows([matched] if matched else [])
                )
            if detail is None:
                period_payload = iwc(
                    f"{code.split('.')[0]} 前十大流通股东报告期、截止日期",
                    limit=10,
                )
                detail = select_latest_top10_report_row(
                    code, period_payload.get("datas") or []
                )
            if detail is None:
                period = report_period_from_rows(detail_rows) or report_period_from_rows([matched] if matched else [])
                query = f"{code.split('.')[0]} 前十大流通股东名称" + (f"[{period}]" if period else "")
                holder_names = iwc(query, limit=20)
                name_rows = holder_names.get("datas") or []
                names = []
                for item in name_rows:
                    if str(item.get("股票代码") or "").upper().split(".")[0] != code.split(".")[0]:
                        continue
                    name = str(item.get("流通股东名称") or item.get("股东名称") or item.get("名称") or "").strip()
                    if name and name not in names:
                        names.append(name)
                if names:
                    if not period:
                        raise SystemExit(
                            f"missing shareholder report period after per-symbol fallback: {code}"
                        )
                    detail = {"股票代码": code, f"前十大流通股东名称(报告期)[{period}]": ", ".join(names)}
            if detail is None:
                # Some stocks have no currently published top-10 holder names.
                # Shareholder badges are evidence-based and therefore fail closed;
                # industry/concept enrichment remains valid and must continue.
                print(
                    f"  {code}: top10 shareholder names unavailable; "
                    f"badges default to false (returned={len(detail_rows)})",
                    flush=True,
                )
            if matched is None:
                matched = detail
            elif detail is not None:
                for key, value in detail.items():
                    if key.startswith("前十大流通股东名称") and value:
                        matched[key] = value
        if matched is not None:
            rows.append(matched)
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