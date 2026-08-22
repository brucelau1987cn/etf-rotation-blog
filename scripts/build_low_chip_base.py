#!/usr/bin/env python3
"""Build low-chip JSON from iWenCai period queries.

Membership stays week/month/quarter AND. Year-line data is an optional UI filter.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-07"

# 新股（上市不足 90 天）没有完整季线周期 K 线，iWenCai 返回的周/月/季
# 获利比例是失真值（0.1~0.5% 极易误入选）。cutoff 用于查询条件与审计。
MIN_LISTING_DAYS = 90
CUTOFF = (datetime.date.fromisoformat(DATE) - datetime.timedelta(days=MIN_LISTING_DAYS)).isoformat()

PERIODS = [
    ("week", "周线收盘获利", f"A股 周线收盘获利小于3%，非ST，非退市，上市日期早于{CUTOFF}"),
    ("month", "月线收盘获利", f"A股 月线收盘获利小于3%，非ST，非退市，上市日期早于{CUTOFF}"),
    ("quarter", "季线收盘获利", f"A股 季线收盘获利小于3%，非ST，非退市，上市日期早于{CUTOFF}"),
]


def fetch(query, page):
    safe = query.replace("/", "_").replace(" ", "_")
    out = Path(f"/tmp/lc_q_{safe}_p{page}.json")
    r = subprocess.run(
        [
            "/root/.hermes/scripts/iwencai-market-query",
            "-q", query,
            "--page", str(page),
            "--limit", "100",
            "--timeout", "90",
        ],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"iWenCai error: {r.stderr[:200]}")
    out.write_text(r.stdout, encoding="utf-8")
    return json.loads(r.stdout)


def paginate(query):
    rows = []
    p = 1
    while True:
        d = fetch(query, p)
        rows.extend(d.get("datas") or [])
        if not d.get("has_more"):
            break
        p += 1
        if p > 10:
            break
    return rows, len(rows)


def period_field(rows, prefix):
    if not rows:
        return None
    for k in rows[0].keys():
        if k.startswith(prefix) and "[" in k:
            return k
    return None


def fetch_year_overlay(codes: list[str]) -> list[dict]:
    """Fetch year-line values for the 3-period pool only.

    A broad year-line screen is capped by iWenCai, so query exact symbols in
    bounded batches and retry any omitted symbol individually.
    """
    compact_date = DATE.replace("-", "")
    by_symbol = {}
    for start in range(0, len(codes), 20):
        batch = codes[start:start + 20]
        bare = [code.split(".")[0] for code in batch]
        rows, _ = paginate("、".join(bare) + f" 年线收盘获利[{compact_date}]")
        by_symbol.update({r.get("股票代码"): r for r in rows if r.get("股票代码")})
        for code in batch:
            if code in by_symbol:
                continue
            rows, _ = paginate(code.split(".")[0] + f" 年线收盘获利[{compact_date}]")
            by_symbol.update({r.get("股票代码"): r for r in rows if r.get("股票代码")})

    field = period_field(list(by_symbol.values()), "年线收盘获利")
    if not field:
        return []
    result = []
    for code in codes:
        row = by_symbol.get(code) or {}
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            continue
        if value > 3:
            continue
        result.append({
            "symbol": code,
            "name": row.get("股票简称") or "",
            "value": round(value, 4),
            "price": float(row.get("最新价") or 0),
            "change_percent": round(float(row.get("最新涨跌幅") or 0), 6),
        })
    return result


def main() -> int:
    periods = {}
    counts = {}
    for label, prefix, query in PERIODS:
        rows, count = paginate(query)
        field = period_field(rows, prefix)
        counts[label] = count
        seen = set()
        period_rows = []
        for r in rows:
            symbol = r.get("股票代码") or ""
            name = r.get("股票简称") or ""
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            v = r.get(field) if field else 0
            try:
                v = float(v)
            except Exception:
                v = 0.0
            period_rows.append({
                "symbol": symbol,
                "name": name,
                "value": round(v, 4),
                "price": float(r.get("最新价") or 0),
                "change_percent": round(float(r.get("最新涨跌幅") or 0), 6),
            })
        periods[label] = period_rows
        print(f"{label}: rows={count} field={field} unique={len(period_rows)}", flush=True)

    week_codes = {r["symbol"] for r in periods["week"]}
    month_codes = {r["symbol"] for r in periods["month"]}
    quarter_codes = {r["symbol"] for r in periods["quarter"]}
    # 年线仅供页面独立开关使用，不参与正式入池交集。
    inter_raw = sorted(week_codes & month_codes & quarter_codes)
    periods["year"] = fetch_year_overlay(inter_raw)
    counts["year"] = len(periods["year"])
    print(f"year: pool={len(inter_raw)} matched={counts['year']}", flush=True)
    # Pre-filter .BJ out of intersection (enrich_low_chip_stocks.py will read it from here)
    excluded_bj_initial = [c for c in inter_raw if c.endswith(".BJ")]
    inter_pre = [c for c in inter_raw if not c.endswith(".BJ")]

    # 审计：无上市日期过滤的周线查询，识别被 cutoff 排除的新股（上市不足 90 天）
    excluded_new_listing = []
    try:
        raw_rows, _ = paginate(f"A股 周线收盘获利小于3%，非ST，非退市")
        raw_week_codes = {r.get("股票代码") or "" for r in raw_rows} - {""}
        excluded_new_listing = sorted(raw_week_codes - week_codes)
    except Exception as exc:  # 审计失败不阻塞主流程
        print(f"new-listing audit skipped: {exc}", flush=True)
    print(f"excluded_new_listing: {excluded_new_listing}", flush=True)
    print(f"intersection_raw: {inter_raw}", flush=True)
    print(f"excluded_bj: {excluded_bj_initial}", flush=True)

    payload = {
        "schema_version": "a-low-profit-v3",
        "data_as_of": DATE,
        "generated_at": datetime.datetime.now(CN).isoformat(timespec="seconds"),
        "source": "iWenCai SkillHub",
        "universe": "沪深A股，非ST，非退市，不含北交所",
        "metric": "收盘获利比例",
        "threshold": 3,
        "counts": counts,
        "periods": periods,
        "intersection_before_filters": inter_raw,
        "intersection": inter_pre,
        "screened_count": len(inter_pre),
        "filters": {
            "exclude_bj": True,
            "excluded_bj": excluded_bj_initial,
            "listing_min_days": MIN_LISTING_DAYS,
            "listing_cutoff": CUTOFF,
            "exclude_new_listing": True,
            "excluded_new_listing": excluded_new_listing,
            "unlock_window": "未来3个月",
            "exclude_unlock_risk": True,
            "excluded_unlock_risk": [],
            "quality_shareholder_definition": "十大流通股东中的社保、基本养老、国家大基金、国新投资、深创投、科威特政府投资局、澳门金融管理局",
            "institutional_shareholder_definition": "十大流通股东中的公募基金、保险资金、阳光私募、QFII/外资机构、香港中央结算及产业资本；与长期资本型优质股东分级展示",
        },
        "enrichments": {},
        "financial_filters": {},
        "shareholder_metrics": {},
    }

    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"data_as_of": DATE, "counts": counts, "intersection_before_filters": inter_raw, "intersection": inter_pre}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())