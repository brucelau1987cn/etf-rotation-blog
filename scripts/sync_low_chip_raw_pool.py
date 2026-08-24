#!/usr/bin/env python3
"""低筹码原始池入库：把三周期筛选的原始结果写入 SQLite / D1。

设计要点
--------
1. **数据来源是已有 JSON 快照**（`public/data/a-low-chip-stocks.json` 或
   `public/data/low-chip-history/*.json`），不重新调用 iWenCai。
   历史快照可以整批回填，零额度消耗。
2. 原始池与下游筛选**解耦**：库里存的是「周/月/季/年线收盘获利 ≤3% 的命中结果」，
   不含任何下游筛选（新股/北交所/解禁/财务/股东）的结果。
   后续要改筛选条件，从库内重算即可，不必重查 iWenCai。
3. 幂等：主键 (trade_date, stock_code, period)，重复导入用 INSERT OR REPLACE。

用法
----
  # 从当前快照入库
  python3 scripts/sync_low_chip_raw_pool.py

  # 从指定快照文件入库
  python3 scripts/sync_low_chip_raw_pool.py --input public/data/low-chip-history/2026-08-21.json

  # 回填全部历史快照
  python3 scripts/sync_low_chip_raw_pool.py --history

  # 只看会写什么，不落库
  python3 scripts/sync_low_chip_raw_pool.py --history --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
DB = ROOT / "data/local/etf-compass.db"
MIGRATION = ROOT / "migrations/0019_low_chip_raw_pool.sql"

PERIODS = ("week", "month", "quarter", "year")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the raw-pool migration (idempotent CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))


def extract_rows(payload: dict) -> tuple[list[tuple], tuple | None]:
    """Turn one snapshot payload into (raw_pool rows, meta row).

    Returns ([], None) when the payload carries no period data, so callers can
    skip incomplete snapshots instead of writing empty days.
    """
    trade_date = payload.get("data_as_of")
    if not trade_date:
        return [], None

    periods = payload.get("periods") or {}
    rows: list[tuple] = []
    for period in PERIODS:
        for entry in periods.get(period) or []:
            code = entry.get("symbol") or ""
            if not code:
                continue
            rows.append((
                trade_date,
                code,
                period,
                entry.get("name") or "",
                _num(entry.get("value")),
                _num(entry.get("price")),
                _num(entry.get("change_percent")),
                payload.get("source") or "iwencai",
            ))

    if not rows:
        return [], None

    counts = payload.get("counts") or {}
    filters = payload.get("filters") or {}
    backfill = payload.get("backfill") or {}
    inter = payload.get("intersection_before_filters")
    if inter is None:
        inter = payload.get("intersection") or []

    meta = (
        trade_date,
        _num(payload.get("threshold")) or 3,
        payload.get("universe") or "",
        filters.get("listing_cutoff") or "",
        filters.get("listing_min_days"),
        counts.get("week"),
        counts.get("month"),
        counts.get("quarter"),
        counts.get("year"),
        len(inter),
        payload.get("generated_at") or "",
        1 if backfill.get("is_backfill") else 0,
        backfill.get("reason") or "",
        payload.get("iwencai_calls"),
    )
    return rows, meta


def _num(value) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_rows(conn: sqlite3.Connection, rows: list[tuple], meta: tuple | None) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO low_chip_raw_pool
           (trade_date, stock_code, period, stock_name, profit_ratio, price,
            change_percent, source)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    if meta is not None:
        conn.execute(
            """INSERT OR REPLACE INTO low_chip_raw_pool_meta
               (trade_date, threshold, universe, listing_cutoff, listing_min_days,
                week_count, month_count, quarter_count, year_count,
                intersection_count, generated_at, is_backfill, backfill_reason,
                iwencai_calls)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            meta,
        )


def snapshot_paths(args) -> list[Path]:
    if args.input:
        return [Path(args.input)]
    if args.history:
        paths = sorted(HISTORY_DIR.glob("????-??-??.json"))
        if DATA.exists():
            paths.append(DATA)
        return paths
    return [DATA]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="single snapshot JSON to import")
    parser.add_argument("--history", action="store_true",
                        help="import every archived snapshot plus the current one")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DB))
    args = parser.parse_args()

    paths = snapshot_paths(args)
    if not paths:
        print("no snapshot to import", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        ensure_schema(conn)
        total_rows = 0
        imported = 0
        skipped: list[str] = []
        for path in paths:
            if not path.exists():
                skipped.append(f"{path.name}: missing")
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                skipped.append(f"{path.name}: bad json ({exc})")
                continue
            rows, meta = extract_rows(payload)
            if not rows:
                skipped.append(f"{path.name}: no period data")
                continue
            if not args.dry_run:
                write_rows(conn, rows, meta)
            total_rows += len(rows)
            imported += 1
            print(f"  {payload.get('data_as_of')}: {len(rows)} rows "
                  f"(week/month/quarter/year)", flush=True)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    print(json.dumps({
        "snapshots_imported": imported,
        "rows": total_rows,
        "skipped": skipped,
        "dry_run": args.dry_run,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
