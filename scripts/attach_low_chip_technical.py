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
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

# 近20个自然日对应的交易日区间：用日期区间语法查询「涨跌幅[A-B]」。
# iWenCai 的「近20日涨跌幅」直接以字段返回（已实测 `涨跌幅[区间]`）。
CHANGE_20D_QUERY_TERM = "近20日涨跌幅"

BATCH_SIZE = 50
TIMEOUT = 120
MAX_WORKERS = 1  # mx-data 限额 code=112，串行 + 退避
MX_QUERY = "{code} RSI 外盘成交量 内盘成交量"
RATE_LIMIT_CODES = (112,)
MAX_RETRIES = 2
RETRY_BACKOFF_S = 5.0

_mx_client = None


def _get_mx():
    global _mx_client
    if _mx_client is None:
        sys.path.insert(0, "/root/.hermes/skills/mx-data")
        from mx_data import MXData
        api_key = os.getenv("MX_APIKEY")
        if not api_key:
            raise RuntimeError("MX_APIKEY env var not set")
        _mx_client = MXData(api_key=api_key)
    return _mx_client


def _parse_vol(s: str) -> float | None:
    s = str(s).strip()
    m = re.match(r'^([\d.]+)\s*亿股$', s)
    if m:
        return float(m.group(1)) * 10000
    m = re.match(r'^([\d.]+)\s*万股$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([\d.]+)\s*万手$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([\d.]+)$', s)
    if m:
        return float(m.group(1))
    return None


def parse_technical_from_mx(tables: list[dict], full_code: str) -> dict:
    """Parse mx-data tables into {rsi, outer_inner_ratio, outer_volume, inner_volume}.

    mx-data RSI sheet is named like "恒瑞医药(600276.SH)的RSI相对强弱指标".
    内盘/外盘 sheet is named like "恒瑞医药(600276.SH)的内盘成交量、外盘成交量".

    Fail-soft: missing fields omitted (consistent with existing contract).
    """
    out: dict = {}
    target_rsi_sheet = None
    target_vol_sheet = None
    for t in tables:
        sn = t.get("sheet_name", "")
        if f"({full_code})" not in sn:
            continue
        if "RSI" in sn:
            target_rsi_sheet = t
        elif "内盘" in sn or "外盘" in sn:
            target_vol_sheet = t

    if target_rsi_sheet:
        # rows: [{date: '2026-09-02', 'RSI相对强弱指标': '20.85'}, ...]
        for row in target_rsi_sheet["rows"]:
            for k, v in row.items():
                if k == "date":
                    continue
                if "RSI" in k:
                    try:
                        out["rsi"] = round(float(str(v).strip()), 3)
                    except (TypeError, ValueError):
                        pass
                    break
            else:
                continue
            break

    if target_vol_sheet:
        # row format depends on mx-data return:
        #   Row 0 = date label, row 1 = 内盘成交量, row 2 = 外盘成交量 (if 3 rows)
        #   or row 0 = 内盘/外盘 split key, row 1 = values (if 2 rows)
        rows = target_vol_sheet["rows"]
        # Try row-key method: {date: '恒瑞医药(600276.SH)', '内盘成交量': '...', '外盘成交量': '...'}
        # (mx-data returns this when there are multiple stocks)
        if rows and isinstance(rows[0], dict):
            inner = rows[0].get("内盘成交量")
            outer = rows[0].get("外盘成交量")
        else:
            inner = outer = None
        if inner is None and outer is None and len(rows) >= 2:
            # Try header-row + values-row
            inner = rows[1].get("内盘成交量")
            outer = rows[1].get("外盘成交量")
        if inner is None and outer is None and len(rows) >= 3:
            # Try 3-row form
            inner = rows[1].get(full_code)
            outer = rows[2].get(full_code)
        inner_v = _parse_vol(inner) if inner is not None else None
        outer_v = _parse_vol(outer) if outer is not None else None
        if outer_v is not None and inner_v is not None and inner_v > 0:
            out["outer_inner_ratio"] = round(outer_v / inner_v, 4)
            out["outer_volume"] = outer_v
            out["inner_volume"] = inner_v
    return out


def attach_for_one(full_code: str) -> tuple[dict, bool]:
    """Returns (tech_dict, success). Retries on rate-limit."""
    bare = full_code.split(".")[0]
    mx = _get_mx()
    q = MX_QUERY.format(code=bare)
    last_err: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = mx.query(q)
        except Exception as exc:
            last_err = f"exception: {exc}"
            time.sleep(RETRY_BACKOFF_S)
            continue
        tables, _, _, err = mx.parse_result(r)
        if err is None:
            return parse_technical_from_mx(tables, full_code), True
        if any(f"状态码 {c}" in err for c in RATE_LIMIT_CODES):
            last_err = err
            print(f"[{full_code}] rate-limited (attempt {attempt+1}/{MAX_RETRIES+1}): {err}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_S * (attempt + 1))
            continue
        print(f"[{full_code}] mx-data parse error: {err}", file=sys.stderr)
        return {}, False
    print(f"[{full_code}] gave up: {last_err}", file=sys.stderr)
    return {}, False


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
    print(json.dumps({
        "status": "ok",
        "total": len(codes),
        "attached": attached,
        "missing": missing,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
