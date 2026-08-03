#!/usr/bin/env python3
"""Attach shareholder and chip concentration metrics to low-chip enrichments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"


def pick(row: dict, prefixes: tuple[str, ...]):
    for prefix in prefixes:
        for key, value in row.items():
            if key.startswith(prefix) and value not in (None, ""):
                return float(value)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/tmp/low_chip_shareholder_metrics.json")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    by_code = {str(row.get("股票代码") or ""): row for row in source.get("datas", [])}
    missing = [code for code in payload["intersection"] if code not in by_code]
    if missing:
        raise SystemExit(f"missing shareholder rows: {missing}")

    for code in payload["intersection"]:
        row = by_code[code]
        holders = pick(row, ("最新股东户数", "总户数["))
        previous_holders = pick(row, ("上期的股东户数", "股东户数(报告期)[", "总户数[20260630]"))
        holder_change_pct = pick(row, ("总户数较上期增长率", "股东户数较上期", "a股户数较上期增长率"))
        average_holding = pick(row, ("最新户均持股数量", "户均持股数量"))
        concentration90 = pick(row, ("集中度90",))
        top10_ratio = pick(row, ("前十大流通股东持股比例合计", "占总股本比"))
        price = pick(row, ("最新价", "收盘价"))
        required = {
            "shareholder_count": holders,
            "average_holding": average_holding,
            "concentration90": concentration90,
            "price": price,
        }
        absent = [name for name, value in required.items() if value is None]
        if absent:
            raise SystemExit(f"{code} missing shareholder fields: {absent}")
        payload["enrichments"][code]["shareholder_metrics"] = {
            "shareholder_count": holders,
            "previous_shareholder_count": previous_holders,
            "shareholder_change_pct": holder_change_pct,
            "average_holding": average_holding,
            "concentration90": concentration90,
            "top10_float_ratio": top10_ratio,
            "price": price,
        }

    payload["shareholder_metrics"] = {
        "source": "iWenCai",
        "fields": ["股东人数", "人均流通股", "较上期变化", "90%筹码集中度", "十大流通股东持股占比"],
    }
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "count": len(payload["intersection"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
