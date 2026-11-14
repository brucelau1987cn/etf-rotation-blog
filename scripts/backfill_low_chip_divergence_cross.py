#!/usr/bin/env python3
"""回填历史快照的 xdiv / xtop（三路金叉型底背离 / 顶背离）。

两组信号都是可精确复算的公式信号：只要有覆盖 data_as_of 的前复权日线，就能按各自
日期重算 MACD/KDJ/RSI 的金叉死叉与背离关系，不估算、不把当日信号写进历史日期。

约束：
  - 每份快照按自身 data_as_of 计算；各中间量因果，第 i 根不受其后数据影响。
  - 行情复用 data/local/jinlong-bars.db 缓存，不重复拉取。
  - 已有 status=ok 的默认跳过（--force 重算）。
  - 单日失败只标该日标的 UNAVAILABLE，不中断整轮回填。
  - 只写 enrichments[code].xdiv / .xtop 与顶层 xdiv_contract。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_jinlong as bars_store  # noqa: E402
import attach_low_chip_divergence_cross as dc  # noqa: E402

HISTORY_DIR = ROOT / "public/data/low-chip-history"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="重算已有的 status=ok 记录")
    parser.add_argument("--day", help="只回填单个日期 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份快照（试跑）")
    parser.add_argument("--window", type=int, default=dc.WINDOW, help="观察窗交易日数")
    args = parser.parse_args()

    paths = sorted(HISTORY_DIR.glob("????-??-??.json"))
    if args.day:
        paths = [p for p in paths if p.stem == args.day]
    if args.limit:
        paths = paths[: args.limit]

    db = bars_store._connect()
    total_ok = total_unavailable = total_bottom = total_top = 0
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
                ((payload.get("enrichments") or {}).get(code, {}).get("xdiv") or {}).get("status") == "ok"
                and ((payload.get("enrichments") or {}).get(code, {}).get("xtop") or {}).get("status") == "ok"
                for code in codes
            ):
                print(f"[{index}/{len(paths)}] {day} already complete, skipped", flush=True)
                continue

            contract = dc.attach(
                payload, day,
                bars_loader=lambda code: bars_store.bars_for(code, day, db),
                window=args.window,
            )
            coverage = contract["coverage"]
            total_ok += coverage["ok"]
            total_unavailable += coverage["unavailable"]
            bottom = sum(
                1 for code in codes
                if (payload["enrichments"].get(code, {}).get("xdiv") or {}).get("hit")
            )
            top = sum(
                1 for code in codes
                if (payload["enrichments"].get(code, {}).get("xtop") or {}).get("hit")
            )
            total_bottom += bottom
            total_top += top
            if contract["status"] != "ok":
                failed_days.append(day)
            if not args.dry_run:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
            print(
                f"[{index}/{len(paths)}] {day} pool={coverage['requested']} ok={coverage['ok']} "
                f"unavailable={coverage['unavailable']} bottom_{args.window}d={bottom} "
                f"top_{args.window}d={top} failures={len(contract['failures'])}",
                flush=True,
            )
    finally:
        db.close()

    print(
        f"backfill done: snapshots={len(paths)} ok={total_ok} unavailable={total_unavailable} "
        f"bottom_hits={total_bottom} top_hits={total_top} failed_days={len(failed_days)} "
        f"dry_run={args.dry_run}",
        flush=True,
    )
    if failed_days:
        print("failed days: " + ", ".join(failed_days), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
