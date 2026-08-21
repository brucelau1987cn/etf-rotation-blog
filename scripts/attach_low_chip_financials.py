#!/usr/bin/env python3
"""Attach latest financial screening metrics to low-chip stock enrichments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"


def field(row: dict, prefix: str):
    for key, value in row.items():
        if key.startswith(prefix):
            return float(value)
    return None


def field_first(row: dict, prefixes: tuple[str, ...]):
    """Try prefixes in order; return the first numeric match."""
    for prefix in prefixes:
        for key, value in row.items():
            if key.startswith(prefix):
                return float(value)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/tmp/low_chip_financial_test_annual.json")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    by_code = {str(row.get("股票代码")): row for row in source.get("datas", [])}
    missing = [code for code in payload["intersection"] if code not in by_code]
    if missing:
        raise SystemExit(f"missing financial rows: {missing}")

    report_period = None
    for code in payload["intersection"]:
        row = by_code[code]
        roe = field_first(row, ("加权净资产收益率", "净资产收益率roe"))
        net_margin = field_first(row, ("销售净利率",))
        cash_flow = field_first(row, ("经营活动产生的现金流量净额",))
        net_profit = field_first(row, ("归属于母公司所有者的净利润", "归母净利润"))
        gross_margin = field_first(row, ("销售毛利率",))
        debt_ratio = field_first(row, ("资产负债率",))
        cash_profit_ratio = (
            float(cash_flow) / float(net_profit) * 100
            if cash_flow is not None and net_profit is not None and net_profit > 0
            else None
        )
        for key in row:
            if "[" in key and key.endswith("]"):
                report_period = key.rsplit("[", 1)[1][:-1]
                break
        payload["enrichments"][code]["financials"] = {
            "report_period": report_period,
            "roe": roe,
            "net_margin": net_margin,
            "operating_cash_flow": cash_flow,
            "net_profit": net_profit,
            "cash_profit_ratio": cash_profit_ratio,
            "gross_margin": gross_margin,
            "debt_ratio": debt_ratio,
        }

    payload["financial_filters"] = {
        "report_period": report_period,
        "roe_min": 15,
        "net_margin_min": 25,
        "cash_profit_ratio_min": 20,
        "gross_margin_min": 15,
        "debt_ratio_max": 30,
        "labels": {
            "roe": "ROE ≥ 15%",
            "net_margin": "净利率 ≥ 25%",
            "cash_profit_ratio": "现金流/净利润 ≥ 20%",
            "gross_margin": "毛利率 ≥ 15%",
            "debt_ratio": "负债率 ≤ 30%",
        },
    }
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "count": len(payload["intersection"]), "period": report_period}, ensure_ascii=False))


if __name__ == "__main__":
    main()
