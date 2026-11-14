#!/usr/bin/env python3
"""回填历史快照的 jinlong（金龙信号）。

金龙是可精确复算的公式信号：只要拿到覆盖 data_as_of 的前复权日线，就能按各自
日期重算出当日 XG，不需要估算，也不会把当日信号写进历史日期。

约束：
  - 每份快照按自身 data_as_of 计算，bars 截止到该日（无未来数据）。
  - 行情落 sqlite 缓存（data/local/jinlong-bars.db），跨快照复用，不重复拉取。
  - 已有 status=ok 的默认跳过（--force 重算）。
  - 单日失败只把该日标的标成 UNAVAILABLE，不中断整轮回填。
  - 只写 enrichments[code].jinlong 与顶层 jinlong_contract。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_jinlong as jl  # noqa: E402

HISTORY_DIR = ROOT / "public/data/low-chip-history"


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

    db = jl._connect()
    total_ok = total_unavailable = total_hits = 0
    failed_days: list[str] = []
    try:
        for index, path in enumerate(paths, 1):
            payload = json.loads(path.read_text(encoding="utf-8"))
            day = str(payload.get("data_as_of") or path.stem)[:10]
            codes = payload.get("intersection") or []
            if not codes:
                print(f"[{index}/{len(paths)}] {day} empty pool, skipped", flush=True)
                continue
            if not args.force and all(
                ((payload.get("enrichments") or {}).get(code, {}).get("jinlong") or {}).get("status") == "ok"
                for code in codes
            ):
                print(f"[{index}/{len(paths)}] {day} already complete, skipped", flush=True)
                continue

            contract = jl.attach(
                payload, day, bars_loader=lambda code: jl.bars_for(code, day, db)
            )
            coverage = contract["coverage"]
            total_ok += coverage["ok"]
            total_unavailable += coverage["unavailable"]
            hits = sum(
                1
                for code in codes
                if (payload["enrichments"].get(code, {}).get("jinlong") or {}).get("hit")
            )
            total_hits += hits
            if contract["status"] != "ok":
                failed_days.append(day)
            if not args.dry_run:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
            print(
                f"[{index}/{len(paths)}] {day} pool={coverage['requested']} ok={coverage['ok']} "
                f"unavailable={coverage['unavailable']} hit_{jl.WINDOW}d={hits} "
                f"failures={len(contract['failures'])}",
                flush=True,
            )
    finally:
        db.close()

    print(
        f"backfill done: snapshots={len(paths)} ok={total_ok} unavailable={total_unavailable} "
        f"hits={total_hits} failed_days={len(failed_days)} dry_run={args.dry_run}",
        flush=True,
    )
    if failed_days:
        print("failed days: " + ", ".join(failed_days), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
