#!/usr/bin/env python3
"""Attach technical screening metrics (RSI / 外盘内盘比) to low-chip enrichments.

字段口径（2026-10-20 东财直连实测，与原 mx-data 逐只一致）：
  - rsi                  → RSI6（Wilder 平滑，period=6）
  - outer_volume         → 外盘成交量（主动买盘）
  - inner_volume         → 内盘成交量（主动卖盘）
  - outer_inner_ratio    → 外盘/内盘

数据源（替代 mx-data「{code} RSI 外盘成交量 内盘成交量」）：
  - RSI：腾讯日K（qfq）自算。mx-data 按 key 计日额度，休眠 key 返 code=113
    导致该阶段长期静默为空；东财/腾讯直连无额度限制。
  - 外盘/内盘：腾讯 qt 行情第 7、8 字段。

缺失即视为不满足（fail-closed）：任一字段取不到则不出现在 technical 字典对应键，
页面筛选时按「不满足该条件」处理。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from em_data_client import (  # noqa: E402
    EastmoneyClient,
    EastmoneyError,
    get_client,
    outer_inner,
    rsi_wilder,
)

_client: EastmoneyClient | None = None

# RSI period. 6 reproduces the Eastmoney/THS RSI6 value exactly (verified
# against mx-data on six stocks, diff 0.00). Do not change to 14.
RSI_PERIOD = 6
KLINE_COUNT = 120


def _get_client() -> EastmoneyClient:
    """Shared Eastmoney/Tencent client (serial + paced requests)."""
    global _client
    if _client is None:
        _client = get_client()
    return _client


def attach_for_one(full_code: str) -> tuple[dict, bool]:
    """Returns (tech_dict, source_ok)."""
    client = _get_client()
    tech: dict = {}

    try:
        closes = client.daily_closes(full_code, count=KLINE_COUNT)
    except EastmoneyError as exc:
        print(f"[{full_code}] daily bars failed: {exc}", file=sys.stderr)
        return {}, False
    rsi_value = rsi_wilder(closes, RSI_PERIOD)
    if rsi_value is not None:
        tech["rsi"] = round(rsi_value, 3)

    try:
        outer, inner = outer_inner(full_code)
    except Exception as exc:  # noqa: BLE001 - network shape varies
        print(f"[{full_code}] outer/inner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return tech, False
    if outer is not None and inner is not None and inner > 0:
        tech["outer_inner_ratio"] = round(outer / inner, 4)
        tech["outer_volume"] = outer
        tech["inner_volume"] = inner

    return tech, bool(tech)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = list(payload.get("intersection") or [])
    if not codes:
        print(json.dumps({
            "status": "skipped",
            "reason": "intersection is empty; nothing to attach",
            "total": 0, "attached": 0, "missing": [],
        }, ensure_ascii=False))
        return 0

    attached = 0
    missing: list[str] = []
    for code in codes:
        tech, ok = attach_for_one(code)
        payload["enrichments"][code]["technical"] = tech
        if not ok or not tech:
            missing.append(code)
        else:
            attached += 1

    payload["technical_filters"] = {
        "rsi_max": 25,
        "outer_inner_ratio_min": 1.5,
        "change_20d_min": 15,
        "labels": {
            "rsi": "RSI ≤ 25%",
            "outer_inner_ratio": "外盘 > 内盘 1.5倍",
            "change_20d": "近20日跌幅 ≥ 15%",
        },
        "missing_codes": missing,
    }

    if args.dry_run:
        print(json.dumps({
            "status": "dry-run",
            "total": len(codes),
            "attached": attached,
            "missing": missing,
        }, ensure_ascii=False))
        return 0

    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # 显式降级状态：数据源不可用时必须可区分「数据源挂了」与「该股无数据」。
    usage = _get_client().usage()
    degraded = bool(usage.get("failures")) or bool(missing)
    print(json.dumps({
        "status": "degraded" if degraded else "ok",
        "source": "eastmoney-direct+tencent",
        "total": len(codes),
        "attached": attached,
        "missing": missing,
        "client": usage,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
