#!/usr/bin/env python3
"""Rebuild a low-chip base payload from the persisted raw-pool SQLite rows."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
DB = ROOT / "data/local/etf-compass.db"
PERIODS = ("week", "month", "quarter", "year")


def build_payload(conn: sqlite3.Connection, trade_date: str) -> dict:
    conn.row_factory = sqlite3.Row
    meta = conn.execute(
        "SELECT * FROM low_chip_raw_pool_meta WHERE trade_date=?", (trade_date,),
    ).fetchone()
    if meta is None:
        raise RuntimeError(f"raw-pool metadata missing for {trade_date}")
    rows = conn.execute(
        """SELECT stock_code,period,stock_name,profit_ratio,price,change_percent
           FROM low_chip_raw_pool WHERE trade_date=? ORDER BY period,stock_code""",
        (trade_date,),
    ).fetchall()
    periods = {period: [] for period in PERIODS}
    for row in rows:
        period = str(row["period"])
        if period not in periods:
            continue
        periods[period].append({
            "symbol": row["stock_code"], "name": row["stock_name"] or "",
            "value": row["profit_ratio"], "price": row["price"] or 0,
            "change_percent": row["change_percent"] or 0,
        })
    sets = [{item["symbol"] for item in periods[period]} for period in ("week", "month", "quarter")]
    intersection = sorted(set.intersection(*sets)) if sets else []
    if len(intersection) != int(meta["intersection_count"]):
        raise RuntimeError(
            f"raw-pool intersection mismatch: {len(intersection)} != {meta['intersection_count']}"
        )
    excluded_bj = [code for code in intersection if code.endswith(".BJ")]
    filtered = [code for code in intersection if not code.endswith(".BJ")]
    return {
        "schema_version": "a-low-profit-v3", "data_as_of": trade_date,
        "generated_at": meta["generated_at"], "source": "iWenCai SkillHub",
        "universe": meta["universe"], "metric": "收盘获利比例",
        "threshold": meta["threshold"],
        "counts": {period: len(periods[period]) for period in PERIODS},
        "periods": periods, "intersection_before_filters": intersection,
        "intersection": filtered, "screened_count": len(filtered),
        "filters": {
            "exclude_bj": True, "excluded_bj": excluded_bj,
            "listing_min_days": meta["listing_min_days"],
            "listing_cutoff": meta["listing_cutoff"],
            "exclude_new_listing": True, "excluded_new_listing": [],
            "unlock_window": "未来3个月", "exclude_unlock_risk": True,
            "excluded_unlock_risk": [],
            "quality_shareholder_definition": "十大流通股东中的社保、基本养老、国家大基金、国新投资、深创投、科威特政府投资局、澳门金融管理局",
            "institutional_shareholder_definition": "十大流通股东中的公募基金、保险资金、阳光私募、QFII/外资机构、香港中央结算及产业资本；与长期资本型优质股东分级展示",
        },
        "enrichments": {}, "financial_filters": {}, "shareholder_metrics": {},
        "recovered_from_raw_pool": {
            "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
            "source": str(DB.relative_to(ROOT)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trade_date")
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--output", default=str(DATA))
    args = parser.parse_args()
    with sqlite3.connect(args.db) as conn:
        payload = build_payload(conn, args.trade_date)
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "ok", "trade_date": args.trade_date,
        "counts": payload["counts"],
        "intersection_before_filters": len(payload["intersection_before_filters"]),
        "intersection": len(payload["intersection"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
