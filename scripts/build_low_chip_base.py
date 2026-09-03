#!/usr/bin/env python3
"""Build low-chip JSON from iWenCai period queries.

Membership stays week/month/quarter AND. Year-line data is an optional UI filter.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
def _resolve_trade_date() -> str:
    # CLI 运行：sys.argv[1]；import 复用（年线脚本/测试）：LOW_CHIP_TRADE_DATE 环境变量；否则默认
    if len(sys.argv) > 1 and Path(sys.argv[0]).name == "build_low_chip_base.py":
        return sys.argv[1]
    return os.environ.get("LOW_CHIP_TRADE_DATE", "2026-08-07")


DATE = _resolve_trade_date()

# 补跑声明：当生成日期晚于数据交易日时（人工补历史快照），必须显式标注，
# 让下游门禁能区分「合法补跑」与「日期错标」。当日正常运行时该字段为 None。
_GENERATED_AT = datetime.datetime.now(CN)
_BACKFILL = None
if _GENERATED_AT.date().isoformat() > DATE:
    _BACKFILL = {
        "is_backfill": True,
        "generated_date": _GENERATED_AT.date().isoformat(),
        "reason": os.environ.get("LOW_CHIP_BACKFILL_REASON", "manual historical backfill"),
    }

# 新股（上市不足 90 天）没有完整季线周期 K 线，iWenCai 返回的周/月/季
# 获利比例是失真值（0.1~0.5% 极易误入选）。cutoff 用于查询条件与审计。
MIN_LISTING_DAYS = 90
CUTOFF = (datetime.date.fromisoformat(DATE) - datetime.timedelta(days=MIN_LISTING_DAYS)).isoformat()

PROFIT_THRESHOLD = 2.0

PERIODS = [
    ("week", "周线收盘获利", f"A股 周线收盘获利小于{PROFIT_THRESHOLD:g}%，非ST，非退市，上市日期早于{CUTOFF}"),
    ("month", "月线收盘获利", f"A股 月线收盘获利小于{PROFIT_THRESHOLD:g}%，非ST，非退市，上市日期早于{CUTOFF}"),
    ("quarter", "季线收盘获利", f"A股 季线收盘获利小于{PROFIT_THRESHOLD:g}%，非ST，非退市，上市日期早于{CUTOFF}"),
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


def _year_profit_value(row: dict) -> float | None:
    """提取年线收盘获利值，兼容批量返回的「收盘获利」与单只返回的「年线收盘获利」。"""
    for key, value in row.items():
        if "[" in key and ("年线收盘获利" in key or key.startswith("收盘获利")):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def fetch_year_overlay(codes: list[str]) -> list[dict]:
    """Fetch year-line values for the 3-period pool only.

    iWenCai 批量查询会把「年线收盘获利」字段简写为「收盘获利」，且可能把部分
    6 位代码误识别成基金(.OF)而遗漏。因此批量后逐只核对：字段缺失或代码遗漏
    的股票单只重查（单只返回完整字段名「年线收盘获利」）。
    """
    compact_date = DATE.replace("-", "")
    by_symbol: dict[str, dict] = {}
    for start in range(0, len(codes), 20):
        batch = codes[start:start + 20]
        bare = [code.split(".")[0] for code in batch]
        rows, _ = paginate("、".join(bare) + f" 年线收盘获利[{compact_date}]")
        for code in batch:
            matched = next((r for r in rows if r.get("股票代码") == code), None)
            if matched is not None and _year_profit_value(matched) is not None:
                by_symbol[code] = matched
                continue
            # 批量遗漏（.OF 误识别）或字段缺失 → 单只重查
            rows2, _ = paginate(code.split(".")[0] + f" 年线收盘获利[{compact_date}]")
            m2 = next((r for r in rows2 if r.get("股票代码") == code), None)
            if m2 is not None and _year_profit_value(m2) is not None:
                by_symbol[code] = m2

    result = []
    for code in codes:
        row = by_symbol.get(code) or {}
        value = _year_profit_value(row)
        if value is None or value > 2.5:
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
            except (TypeError, ValueError):
                continue
            # 服务端查询负责初筛，本地门禁再次保证严格小于阈值。
            if not 0 <= v < PROFIT_THRESHOLD:
                continue
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
    # 年线 overlay 已彻底移到流水线最后的 attach_low_chip_year_line.py（先入库→其他条件→年线最后）。
    # 这里 periods["year"] 留空，由最后一步回填；年线失败不阻塞入库。
    periods["year"] = []
    counts["year"] = 0
    print(f"year: pool={len(inter_raw)} matched=0 (deferred to year-line step)", flush=True)
    # Pre-filter .BJ out of intersection (enrich_low_chip_stocks.py will read it from here)
    excluded_bj_initial = [c for c in inter_raw if c.endswith(".BJ")]
    inter_pre = [c for c in inter_raw if not c.endswith(".BJ")]

    # 审计：无上市日期过滤的周线查询，识别被 cutoff 排除的新股（上市不足 90 天）
    excluded_new_listing = []
    try:
        raw_rows, _ = paginate(f"A股 周线收盘获利小于{PROFIT_THRESHOLD:g}%，非ST，非退市")
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
        "generated_at": _GENERATED_AT.isoformat(timespec="seconds"),
        "source": "iWenCai SkillHub",
        "universe": "沪深A股，非ST，非退市，不含北交所",
        "metric": "收盘获利比例",
        "threshold": PROFIT_THRESHOLD,
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
    if _BACKFILL is not None:
        payload["backfill"] = _BACKFILL

    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"data_as_of": DATE, "counts": counts, "intersection_before_filters": inter_raw, "intersection": inter_pre}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())