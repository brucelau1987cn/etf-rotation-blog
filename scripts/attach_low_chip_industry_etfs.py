#!/usr/bin/env python3
"""Attach industry ETF candidates from the formal 91-ETF garden pool.

The mapping starts from each low-chip stock's SW2021 level-2 industry. Every
returned ETF is resolved from etf-garden-pool.json::all_rows; codes outside the
formal pool cannot enter the output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
POOL = ROOT / "public/data/etf-garden-pool.json"
EXPECTED_POOL_COUNT = 91
TOP_N = 3
SOURCE_POOL = "etf-garden-formal-91"

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


def attach_industry_etfs(data_path: Path = DATA, pool_path: Path = POOL) -> dict[str, int]:
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

    tmp = data_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(data_path)
    return {"stocks": len(symbols), "covered": covered, "pool_count": pool_count}


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
