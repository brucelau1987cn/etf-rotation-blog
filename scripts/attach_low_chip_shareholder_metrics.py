#!/usr/bin/env python3
"""Attach shareholder and chip concentration metrics to low-chip enrichments.

All display fields now come from Eastmoney:
  --holder-em     /tmp/low_chip_holder_em.json  (HSF10: 户数/变化/集中度/流通股东/报告期)
  --main-force    /tmp/low_chip_main_force.json (datacenter: 主力控盘/机构参与度)
iWenCai is no longer a source for shareholder display data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holder-em", default="/tmp/low_chip_holder_em.json")
    parser.add_argument("--main-force", default="/tmp/low_chip_main_force.json")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    holder_em = json.loads(Path(args.holder_em).read_text(encoding="utf-8"))
    main_force_source = json.loads(Path(args.main_force).read_text(encoding="utf-8"))
    mf_by_code = {str(row.get("SECURITY_CODE") or ""): row for row in main_force_source}

    missing = [code for code in payload["intersection"] if code not in holder_em]
    if missing:
        # 东财股东/户数缺失不再中断发布：缺的股票字段填空、页面显示「—」。
        print(
            f"  shareholder_metrics: {len(missing)} 只缺东财户数数据，字段留空（不阻断）",
            flush=True,
        )

    for code in payload["intersection"]:
        em = holder_em.get(code) or {}
        main_force = None
        main_force_label = None
        mf_row = mf_by_code.get(code.split(".")[0])
        if mf_row:
            org = mf_row.get("ORG_PARTICIPATE")
            if org is not None:
                main_force = round(org * 100, 2)
                main_force_label = mf_row.get("PARTICIPATE_TYPE_CN")
        required = {
            "shareholder_count": em.get("holder_total"),
            "price": em.get("price"),
        }
        absent = [name for name, value in required.items() if value is None]
        if absent:
            # 字段缺失不再中断：该股 shareholder_metrics 用空值兜底。
            print(f"  {code}: 缺股东字段 {absent}，留空显示「—」", flush=True)
        payload["enrichments"][code]["shareholder_metrics"] = {
            "shareholder_count": em.get("holder_total"),
            "previous_shareholder_count": em.get("previous_holder"),
            "shareholder_change_pct": em.get("total_ratio"),
            "average_holding": em.get("avg_free_shares"),
            "top10_float_ratio": em.get("freehold_ratio"),
            "main_force": main_force,
            "main_force_label": main_force_label,
            "chip_focus": em.get("focus"),
            "report_period": em.get("end_date"),
            "price": em.get("price"),
        }

    payload["shareholder_metrics"] = {
        "source": "Eastmoney HSF10 + datacenter",
        "fields": ["股东人数", "较上期变化", "筹码集中度", "十大流通股东", "报告期", "人均流通股", "主力控盘(机构参与度)"],
        "note": "股东/筹码/报告期来自东方财富F10股东研究；主力控盘来自东财千股千评(机构参与度)；iWenCai仅用于入池筛选",
    }
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "count": len(payload["intersection"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()