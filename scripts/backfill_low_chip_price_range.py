#!/usr/bin/env python3
"""回填历史快照的 price_range（一年内/三年内最低价，不复权）。

历史快照的窗口起点各自不同（每份以自身 data_as_of 为准），但底层日线是同一
条不复权序列。因此每只股票只拉一次长序列（1200 根，覆盖 2021-11 以来，足以
满足最老快照的三年窗口 2023-07-31），再对每个快照日期分别截断计算。

约束：
  - 严格 no-lookahead：窗口上界就是该快照的 data_as_of。
  - 已有 status=ok 的 price_range 默认跳过（--force 重算）。
  - 单只失败只记 UNAVAILABLE + reason，不估算、不中断整轮回填。
  - 只写 enrichments[code].price_range 与顶层 price_range_contract_backfill。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_price_range as pr  # noqa: E402

HISTORY_DIR = ROOT / "public/data/low-chip-history"
LONG_COUNT = 1200
REQUEST_INTERVAL_S = 0.12


def long_bars(symbol: str) -> tuple[list[dict], str]:
    """拉一条长不复权序列（不改 pr 模块常量的副作用：临时覆盖后还原）。

    end 必须传「今天」而不是 2099：同花顺按 end 的年份决定拉哪几个年份
    (end-3 … end)，传 2099 会去请求 2096–2099 四个空端点，腾讯 501 时
    无备源可用 —— 2026-11-08 首轮回填 112 只因此整只失败。
    上限语义由 price_range_metrics 里的 as_of 截断保证。
    """
    original = pr.TENCENT_COUNT
    pr.TENCENT_COUNT = LONG_COUNT
    try:
        return pr.load_bars(symbol, date.today().isoformat())
    finally:
        pr.TENCENT_COUNT = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="重算已有的 status=ok 记录")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（试跑）")
    args = parser.parse_args()

    snapshots: dict[str, dict] = {}
    need: dict[str, set[str]] = {}
    for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        day = str(payload.get("data_as_of") or "")[:10]
        if len(day) != 10:
            continue
        snapshots[day] = payload
        for code in payload.get("intersection") or []:
            existing = (payload.get("enrichments") or {}).get(code, {}).get("price_range") or {}
            if existing.get("status") == "ok" and not args.force:
                continue
            need.setdefault(code, set()).add(day)

    symbols = sorted(need)
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"snapshots={len(snapshots)} symbols={len(symbols)} "
          f"rows={sum(len(need[s]) for s in symbols)}", flush=True)

    failed: list[str] = []
    written = 0
    # 只重算仍缺有效记录的代码；已有 ok 的不再拉一次网络（本轮失败项过多时
    # 直接重跑即可，不必 --force 全量重算）。
    for index, symbol in enumerate(symbols, 1):
        try:
            bars, source = long_bars(symbol)
        except Exception as exc:  # noqa: BLE001 - 单只隔离
            failed.append(symbol)
            print(f"[{index}/{len(symbols)}] {symbol} fetch failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            for day in need[symbol]:
                enr = snapshots[day].setdefault("enrichments", {}).setdefault(symbol, {})
                enr["price_range"] = {
                    "status": "UNAVAILABLE",
                    "reason": f"backfill {type(exc).__name__}: {exc}"[:300],
                    "adjustment": pr.ADJUSTMENT,
                    "as_of": day,
                }
                written += 1
            time.sleep(REQUEST_INTERVAL_S)
            continue
        for day in sorted(need[symbol]):
            payload = snapshots[day]
            metrics = pr.price_range_metrics(bars, day, pr.snapshot_price(payload, symbol))
            metrics["source"] = source
            enr = payload.setdefault("enrichments", {}).setdefault(symbol, {})
            enr["price_range"] = metrics
            written += 1
        if index % 50 == 0:
            print(f"progress {index}/{len(symbols)} rows={written} failed={len(failed)}", flush=True)
        time.sleep(REQUEST_INTERVAL_S)

    for day, payload in snapshots.items():
        codes = list(payload.get("intersection") or [])
        covered = sum(
            1 for code in codes
            if ((payload.get("enrichments") or {}).get(code, {}).get("price_range") or {}).get("status") == "ok"
        )
        codes_failed = [
            code for code in codes
            if ((payload.get("enrichments") or {}).get(code, {}).get("price_range") or {}).get("status") != "ok"
        ]
        payload["price_range_contract"] = {
            "version": "2026-11-08",
            "as_of": day,
            "adjustment": pr.ADJUSTMENT,
            "windows": {"one_year_days": 365, "three_year_years": 3},
            "low_basis": "daily low (最低价)",
            "price_basis": "snapshot periods price (不复权)",
            "requested": len(codes),
            "covered": covered,
            "failed": codes_failed,
            "status": "ok" if not codes_failed else ("blocked" if covered == 0 else "degraded"),
            "note": "不复权真实成交价；含分红送股的区间涨幅与复权口径存在偏差。",
        }
        if not args.dry_run:
            (HISTORY_DIR / f"{day}.json").write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

    print(json.dumps({
        "status": "dry-run" if args.dry_run else "ok",
        "snapshots": len(snapshots),
        "symbols": len(symbols),
        "rows_written": written,
        "failed_symbols": failed,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
