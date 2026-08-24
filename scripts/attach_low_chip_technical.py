#!/usr/bin/env python3
"""Attach technical screening metrics (RSI / 外盘内盘比 / 近20日跌幅) to low-chip enrichments.

字段口径（iWenCai 实测 2026-08-24）：
  - rsi[YYYYMMDD]             → RSI 指标值
  - 涨跌幅[区间YYYYMMDD-YYYYMMDD] → 近20日累计涨跌幅（%）
  - 外盘成交量 / 内盘成交量      → 主动买盘 / 主动卖盘（日数据），比值 = 外盘/内盘

缺失即视为不满足（fail-closed）：任一字段取不到则不出现在 technical 字典对应键，
页面筛选时按「不满足该条件」处理。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

# 近20个自然日对应的交易日区间：用日期区间语法查询「涨跌幅[A-B]」。
# iWenCai 的「近20日涨跌幅」直接以字段返回（已实测 `涨跌幅[区间]`）。
CHANGE_20D_QUERY_TERM = "近20日涨跌幅"
TECH_QUERY_TERMS = "RSI、近20日涨跌幅、外盘成交量、内盘成交量"

BATCH_SIZE = 50
TIMEOUT = 120


def iwc(query: str, page: int = 1, limit: int = 200, timeout: int = TIMEOUT) -> dict:
    r = subprocess.run(
        [
            "/root/.hermes/scripts/iwencai-market-query",
            "-q", query,
            "--page", str(page),
            "--limit", str(limit),
            "--timeout", str(timeout),
        ],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"iWenCai error: {r.stderr[:200]}")
    return json.loads(r.stdout)


def field_first(row: dict, prefixes: tuple[str, ...]):
    """Return first numeric value among keys starting with any prefix."""
    for prefix in prefixes:
        for key, value in row.items():
            if key.startswith(prefix) and value not in (None, "", "-", "--"):
                try:
                    return float(str(value).replace(",", "").replace("%", ""))
                except (TypeError, ValueError):
                    continue
    return None


def parse_technical(row: dict) -> dict:
    out: dict = {}
    rsi = field_first(row, ("rsi", "RSI"))
    if rsi is not None:
        out["rsi"] = round(rsi, 3)
    change_20d = field_first(row, ("涨跌幅", "近20日涨跌幅", "区间涨跌幅"))
    if change_20d is not None:
        out["change_20d"] = round(change_20d, 3)
    outer = field_first(row, ("外盘成交量", "外盘"))
    inner = field_first(row, ("内盘成交量", "内盘"))
    if outer is not None and inner is not None and inner > 0:
        out["outer_inner_ratio"] = round(outer / inner, 4)
        out["outer_volume"] = outer
        out["inner_volume"] = inner
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = list(payload.get("intersection") or [])
    if not codes:
        # technical 是页面附加筛选字段，不参与入池。缺数据时 fail-soft：
        # 不阻塞整条低筹码发布链路（缺失即过滤，前端已兜底）。
        print(json.dumps({
            "status": "skipped",
            "reason": "intersection is empty; nothing to attach",
            "total": 0, "attached": 0, "missing": [],
        }, ensure_ascii=False))
        return 0

    bare_codes = [c.split(".")[0] for c in codes]
    by_code: dict[str, dict] = {}
    missing: list[str] = []

    # 分批查询，避免单次查询股票数过大。
    for start in range(0, len(bare_codes), BATCH_SIZE):
        batch = bare_codes[start:start + BATCH_SIZE]
        q = "、".join(batch) + " " + TECH_QUERY_TERMS
        try:
            d = iwc(q, limit=max(200, len(batch) * 3))
        except RuntimeError as exc:
            raise SystemExit(f"technical batch query failed: {exc}")
        rows = d.get("datas") or []
        for r in rows:
            code = str(r.get("股票代码") or "").upper()
            if code and code not in by_code:
                by_code[code] = r

    # 缺失的股票按单只补查一次（容错），仍缺则记入 missing。
    for code in codes:
        full = code.upper()
        if full in by_code or code in by_code:
            continue
        try:
            d = iwc(f"{code.split('.')[0]} {TECH_QUERY_TERMS}", limit=10)
            for r in d.get("datas") or []:
                rc = str(r.get("股票代码") or "").upper()
                if rc == full:
                    by_code[full] = r
                    break
        except RuntimeError:
            pass
        if full not in by_code and code not in by_code:
            missing.append(code)

    attached = 0
    for code in codes:
        full = code.upper()
        row = by_code.get(full) or by_code.get(code)
        if row is None:
            # 完全查不到 → technical 置为空，页面按「不满足」处理。
            payload["enrichments"][code]["technical"] = {}
            continue
        tech = parse_technical(row)
        payload["enrichments"][code]["technical"] = tech
        if tech:
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
    print(json.dumps({
        "status": "ok",
        "total": len(codes),
        "attached": attached,
        "missing": missing,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
