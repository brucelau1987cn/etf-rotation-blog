#!/usr/bin/env python3
"""回填历史快照的 chip_concentration（90% 筹码集中度）。

iWenCai 的「集中度90[YYYYMMDD]」支持按历史日期取值（实测 000401 在
2026-11-06/11-11/11-12/11-13 分别返回 8.7/8.5/8.5/8.4），因此历史快照可以
按各自 data_as_of 逐日回填，不需要估算。

约束：
  - 每份快照的 data_as_of 须为有效的 YYYY-MM-DD 且等于文件名日期。
  - 日期无效的快照不查询、不写入；处理其他日期后以非零状态退出。
  - 已有 status=ok 的默认跳过（--force 重算）。
  - 单日查询失败只把该日标的标成 UNAVAILABLE，不中断整轮回填。
  - 只写 enrichments[code].chip_concentration 与顶层 chip_concentration_contract。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_chip_concentration as cc  # noqa: E402

HISTORY_DIR = ROOT / "public/data/low-chip-history"
REQUEST_INTERVAL_S = 0.5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="重算已有的 status=ok 记录")
    parser.add_argument("--day", help="只回填单个日期 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份快照（试跑）")
    args = parser.parse_args()

    paths = sorted(HISTORY_DIR.glob("????-??-??.json"))
    if args.day:
        paths = [p for p in paths if p.stem == args.day]
    if args.limit:
        paths = paths[: args.limit]

    total_ok = total_unavailable = 0
    failed_days: list[str] = []
    date_errors = 0
    for index, path in enumerate(paths, 1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        day = payload.get("data_as_of")
        try:
            cc._compact_date(day)
        except ValueError:
            print(f"{path.name}: data_as_of must be a valid YYYY-MM-DD date", file=sys.stderr, flush=True)
            date_errors += 1
            continue
        if day != path.stem:
            print(f"{path.name}: data_as_of {day} does not match filename date", file=sys.stderr, flush=True)
            date_errors += 1
            continue
        codes = payload.get("intersection") or []
        if not codes:
            print(f"[{index}/{len(paths)}] {day} empty pool, skipped", flush=True)
            continue
        if not args.force and all(
            ((payload.get("enrichments") or {}).get(code, {}).get("chip_concentration") or {}).get("status") == "ok"
            for code in codes
        ):
            print(f"[{index}/{len(paths)}] {day} already complete, skipped", flush=True)
            continue

        contract = cc.attach(payload, day)
        coverage = contract["coverage"]
        total_ok += coverage["ok"]
        total_unavailable += coverage["unavailable"]
        if contract["status"] != "ok":
            failed_days.append(day)
        if not args.dry_run:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        print(
            f"[{index}/{len(paths)}] {day} pool={coverage['requested']} ok={coverage['ok']} "
            f"unavailable={coverage['unavailable']} failed_batches={len(contract['failed_batches'])}",
            flush=True,
        )
        time.sleep(REQUEST_INTERVAL_S)

    print(
        f"backfill done: snapshots={len(paths)} ok={total_ok} unavailable={total_unavailable} "
        f"failed_days={len(failed_days)} date_errors={date_errors} dry_run={args.dry_run}",
        flush=True,
    )
    if failed_days:
        print("failed days: " + ", ".join(failed_days), flush=True)
    return 1 if date_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
