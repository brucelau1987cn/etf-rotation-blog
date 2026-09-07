#!/usr/bin/env python3
"""Attach industry ETF candidates from the formal 91-ETF garden pool.

The mapping starts from each low-chip stock's SW2021 level-2 industry. Every
returned ETF is resolved from etf-garden-pool.json::all_rows; codes outside the
formal pool cannot enter the output.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
POOL = ROOT / "public/data/etf-garden-pool.json"
EXPECTED_POOL_COUNT = 91
TOP_N = 3
SOURCE_POOL = "etf-garden-formal-91"

IWENCIAI_QUERY = "/root/.hermes/scripts/iwencai-market-query"

# SW2021 level-2 industry -> ordered themes in the formal ETF pool.
# The first exact/specific product wins; broader parent products follow.
INDUSTRY_THEME_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "工程机械": (("工程机械", "exact"),),
    "装修建材": (("建材", "alias"),),
    "电力": (("电力", "exact"),),
    "光伏设备": (("光伏", "alias"),),
    "电网设备": (("电网设备", "exact"),),
    "乘用车": (("汽车", "parent"), ("智能驾驶", "related")),
    "汽车零部件": (("汽车", "parent"), ("智能驾驶", "related")),
    "半导体": (("半导体", "exact"),),
    "医疗器械": (("医疗器械", "exact"), ("医疗", "parent")),
    "化学制品": (("化工", "alias"),),
    "化学制药": (("医药", "parent"), ("创新药", "related")),
    "电池": (("动力电池", "alias"), ("储能", "related")),
    "消费电子": (("消费电子", "exact"), ("电子", "parent")),
    "软件开发": (("软件", "alias"),),
    "游戏Ⅱ": (("游戏", "alias"),),
    "白酒Ⅱ": (("白酒", "alias"),),
    "证券Ⅱ": (("证券", "alias"), ("证券保险", "parent")),
    "银行Ⅱ": (("银行", "alias"),),
    "房地产开发": (("房地产", "alias"),),
    "煤炭开采": (("煤炭", "alias"),),
    "钢铁": (("钢铁", "exact"),),
    "通信设备": (("通信", "parent"),),
}


def _full_code(row: dict[str, Any]) -> str:
    code = str(row.get("code") or "").strip()
    market = str(row.get("market") or "").lower()
    suffix = "SH" if market in {"sh", "xshg"} or code.startswith(("5", "6")) else "SZ"
    return f"{code}.{suffix}"


def match_industry_etfs(industry: str, pool_rows: list[dict], top_n: int = TOP_N) -> list[dict]:
    targets = INDUSTRY_THEME_MAP.get(str(industry or "").strip(), ())
    if not targets:
        return []
    formal = [row for row in pool_rows if row.get("tier") == "formal"]
    by_theme: dict[str, list[dict]] = {}
    for row in formal:
        by_theme.setdefault(str(row.get("theme") or "").strip(), []).append(row)

    matched: list[dict] = []
    seen: set[str] = set()
    for theme, match_type in targets:
        for row in by_theme.get(theme, []):
            full_code = _full_code(row)
            if full_code in seen:
                continue
            seen.add(full_code)
            matched.append({
                "code": full_code,
                "name": str(row.get("name") or ""),
                "theme": str(row.get("theme") or ""),
                "category": str(row.get("category") or ""),
                "match_type": match_type,
                "source_pool": SOURCE_POOL,
            })
            if len(matched) >= top_n:
                return matched
    return matched


def fetch_etf_profit_ratios(codes: list[str], trade_date: str) -> dict[str, float | None]:
    """Query iWenCai for each ETF's 收盘获利 (closing profit ratio) on trade_date.

    Uses the same `{code} 收盘获利[{YYYYMMDD}]` query as the low-chip screen.
    iWenCai returns the ratio with the date suffix as key; batch multi-code query
    works ("code1,code2 收盘获利[20260904]" -> one row per code). Fail-soft:
    any query error returns empty dict so enrichment never blocks on quota.
    """
    if not codes:
        return {}
    # iWenCai 批量查询不稳: 带后缀代码返回0、6位批量超8只丢行(实测17只只回9只)。
    # 逐只查询 100% 命中(实测), 每只一次调用, 失败跳过不阻塞。
    bare = [c.split(".")[0] for c in codes]
    result: dict[str, float | None] = {}
    for code in bare:
        query = f"{code} 收盘获利[{trade_date}]"
        try:
            r = subprocess.run(
                [IWENCIAI_QUERY, "-q", query, "--limit", "100", "--timeout", "60"],
                capture_output=True, text=True, check=False, timeout=90,
            )
        except (subprocess.TimeoutExpired, OSError):
            print(f"[iwencai] ETF profit query failed (timeout/OSError): {code}")
            continue
        if r.returncode != 0:
            print(f"[iwencai] ETF profit query rc={r.returncode}: {code}")
            continue
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"[iwencai] ETF profit query returned non-JSON: {code}")
            continue
        key = f"收盘获利[{trade_date}]"
        for row in data.get("datas") or []:
            row_code = str(row.get("基金代码") or "").strip()
            if row_code.startswith(code):
                val = row.get(key)
                try:
                    result[row_code] = float(val) if val is not None else None
                except (TypeError, ValueError):
                    result[row_code] = None
                break
    return result


def attach_industry_etfs(data_path: Path = DATA, pool_path: Path = POOL, trade_date: str | None = None) -> dict[str, int]:
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    pool_count = int((pool.get("summary") or {}).get("universe_count") or 0)
    if pool_count != EXPECTED_POOL_COUNT:
        raise ValueError(f"industry ETF mapping requires exactly 91 formal pool rows, got {pool_count}")

    pool_rows = pool.get("all_rows") or []
    formal_rows = [row for row in pool_rows if row.get("tier") == "formal"]
    formal_nonempty = [
        row for row in formal_rows if str(row.get("code") or "").strip()
    ]
    formal_codes = {str(row.get("code") or "").strip() for row in formal_nonempty}
    if (
        len(formal_rows) != EXPECTED_POOL_COUNT
        or len(formal_nonempty) != EXPECTED_POOL_COUNT
        or len(formal_codes) != EXPECTED_POOL_COUNT
    ):
        raise ValueError(
            "industry ETF mapping requires 91 actual formal rows, 91 non-empty codes, "
            f"and 91 unique codes; got rows={len(formal_rows)} "
            f"nonempty={len(formal_nonempty)} unique_codes={len(formal_codes)}"
        )
    symbols = payload.get("intersection") or []
    enrichments = payload.setdefault("enrichments", {})
    covered = 0
    for code in symbols:
        rec = enrichments.setdefault(code, {})
        level2 = rec.get("industry_level2") or {}
        industry = level2.get("name") if isinstance(level2, dict) else ""
        valid_industry = (
            rec.get("industry_standard") == "SW2021"
            and bool(industry)
            and industry != "待补充"
        )
        candidates = match_industry_etfs(str(industry), pool_rows) if valid_industry else []
        rec["industry_etfs"] = candidates
        if not valid_industry:
            rec["industry_etf_status"] = "industry_missing"
        else:
            rec["industry_etf_status"] = "matched" if candidates else "uncovered"
        rec["industry_etf_pool_count"] = pool_count
        if candidates:
            covered += 1

    # 行业ETF 获利盘（快照日收盘获利）——iWenCai 批量查询，fail-soft。
    if trade_date is None:
        trade_date = str(payload.get("data_as_of") or "").replace("-", "")
    profit_map: dict[str, float | None] = {}
    if trade_date:
        all_etf_codes: list[str] = []
        for code in symbols:
            for etf in enrichments.get(code, {}).get("industry_etfs") or []:
                c = str(etf.get("code") or "").strip()
                if c and c not in all_etf_codes:
                    all_etf_codes.append(c)
        if all_etf_codes:
            profit_map = fetch_etf_profit_ratios(all_etf_codes, trade_date)
            hit = sum(1 for v in profit_map.values() if v is not None)
            print(f"[iwencai] ETF profit ratios: {hit}/{len(all_etf_codes)} on {trade_date}")
    for code in symbols:
        for etf in enrichments.get(code, {}).get("industry_etfs") or []:
            c = str(etf.get("code") or "").strip()
            etf["profit_ratio"] = profit_map.get(c) if c in profit_map else None

    tmp = data_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(data_path)
    return {
        "stocks": len(symbols),
        "covered": covered,
        "pool_count": pool_count,
        "profit_filled": sum(1 for v in profit_map.values() if v is not None) if profit_map else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--pool", type=Path, default=POOL)
    args = parser.parse_args()
    result = attach_industry_etfs(args.input, args.pool)
    print(
        f"Industry ETF mapping: {result['covered']}/{result['stocks']} covered; "
        f"candidate pool={result['pool_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
