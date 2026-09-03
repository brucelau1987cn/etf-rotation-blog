#!/usr/bin/env python3
"""Attach latest financial screening metrics to low-chip stock enrichments (Fuyao structured source).

财务指标从 iWenCai 改为 Fuyao 结构化接口（fuyao.aicubes.cn）：
- 5 个现成指标：roe / net_margin / gross_margin / debt_ratio / cash_profit_ratio
- cash_profit_ratio 由 Fuyao 直接给出，不再用「经营现金流/净利润」自算
- 报告期按日期推断最新已披露期，未披露(5003)回退上一期（最多 2 级）
- 失败 fail-soft：某只无数据时 financials 留空，不阻断发布
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "public/data/a-low-chip-stocks.json"
CN = ZoneInfo("Asia/Shanghai")

from audit_low_chip_fuyao import FuyaoClient, load_api_key  # noqa: E402

REPORT_SUFFIX = {"1": "0331", "2": "0630", "3": "0930", "4": "1231"}
FINANCIAL_FIELDS = ("roe", "net_margin", "gross_margin", "debt_ratio", "cash_profit_ratio")


def _latest_report(now: dt.datetime) -> str:
    """按当前日期推断最新已披露报告期（A股财报披露节奏）。"""
    month, year = now.month, now.year
    if month <= 4:
        return f"{year - 1}-4"  # 上一年年报
    if month <= 8:
        return f"{year}-1"      # 当年一季报
    if month <= 10:
        return f"{year}-2"      # 当年半年报
    return f"{year}-3"          # 当年三季报


def _prev_report(report: str) -> str:
    year, quarter = report.split("-")
    q = int(quarter)
    return f"{int(year) - 1}-4" if q == 1 else f"{year}-{q - 1}"


def _report_to_period(report: str) -> str:
    year, quarter = report.split("-")
    return f"{year}{REPORT_SUFFIX[quarter]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=None, help="目标报告期 YYYY-Q，缺省按日期推断最新")
    parser.add_argument("--input", default=None, help="(兼容) 旧 iWenCai 输入文件，已不再使用")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = payload["intersection"]
    client = FuyaoClient(load_api_key(), qps=2.0)

    target = args.report or _latest_report(dt.datetime.now(CN))
    report_period = None
    ok = 0
    for code in codes:
        fin: dict = {}
        report = target
        for _ in range(3):  # 目标未披露(5003)回退上一报告期，最多 2 级
            try:
                got = client.financials(code, report)
                if got:
                    fin = got
                    break
            except RuntimeError:
                pass
            report = _prev_report(report)
        if not fin:
            payload["enrichments"][code]["financials"] = {}
            print(f"  financials: {code} 无 Fuyao 数据，字段留空（不阻断）", flush=True)
            continue
        ok += 1
        period = _report_to_period(report)
        report_period = report_period or period
        payload["enrichments"][code]["financials"] = {
            "report_period": period,
            **{field: fin.get(field) for field in FINANCIAL_FIELDS},
        }

    payload["financial_filters"] = {
        "report_period": report_period,
        "roe_min": 15,
        "net_margin_min": 15,
        "cash_profit_ratio_min": 20,
        "gross_margin_min": 15,
        "debt_ratio_max": 30,
        "labels": {
            "roe": "ROE ≥ 15%",
            "net_margin": "净利率 ≥ 15%",
            "cash_profit_ratio": "现金流/净利润 ≥ 20%",
            "gross_margin": "毛利率 ≥ 15%",
            "debt_ratio": "负债率 ≤ 30%",
        },
    }
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(
        {"status": "ok", "count": ok, "total": len(codes), "period": report_period, "source": "fuyao"},
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
