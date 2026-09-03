#!/usr/bin/env python3
"""年线收盘获利 overlay —— 低筹码流水线最后一步，单独回填 payload 的 periods["year"]。

设计：入库（三周期 ≤2%）→ 其他 enrichment → 年线排最后（本脚本）。
年线可选：quota 耗尽 / 上游异常 / 空交集时 fail-soft，不阻塞、不回滚前面任何产出。

用法：
    python attach_low_chip_year_line.py [TRADE_DATE]
    TRADE_DATE 缺省时回退 payload 的 data_as_of。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402  (复用 paginate / _year_profit_value / fetch_year_overlay)


def main() -> int:
    payload = json.loads(base.DATA.read_text(encoding="utf-8"))
    trade_date = sys.argv[1] if len(sys.argv) > 1 else payload.get("data_as_of") or ""
    if trade_date:
        base.DATE = trade_date  # fetch_year_overlay 内部用模块级 DATE 拼查询

    inter = payload.get("intersection") or payload.get("intersection_before_filters") or []
    if not inter:
        print("year overlay skipped: empty intersection", flush=True)
        return 0

    try:
        year = base.fetch_year_overlay(inter)
    except Exception as exc:  # noqa: BLE001 年线排最后，失败不阻塞
        print(f"year overlay skipped (fail-soft): {exc}", flush=True)
        return 0

    payload["periods"]["year"] = year
    payload["counts"]["year"] = len(year)
    base.DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"year overlay: pool={len(inter)} matched={len(year)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
