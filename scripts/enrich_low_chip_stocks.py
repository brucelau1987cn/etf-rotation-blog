#!/usr/bin/env python3
"""Enrich the low-chip intersection with board, industry, unlock and shareholder labels."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

if __package__:
    from .query_low_chip_unlocks import blocked, validate_cache
else:
    _unlock_path = Path(__file__).resolve().with_name("query_low_chip_unlocks.py")
    _unlock_spec = importlib.util.spec_from_file_location("_low_chip_unlock_gate", _unlock_path)
    if _unlock_spec is None or _unlock_spec.loader is None:
        raise ImportError(f"cannot load unlock gate from {_unlock_path}")
    _unlock_module = importlib.util.module_from_spec(_unlock_spec)
    _unlock_spec.loader.exec_module(_unlock_module)
    blocked, validate_cache = _unlock_module.blocked, _unlock_module.validate_cache

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
# 动态读取 fetch_low_chip_enrichments.py 生成的全部 profile 分页文件。
# 初筛池规模随市场波动（月末可超 20 只 → lc_p5+），硬编码 range(1,5) 会漏读
# 后段分页导致行业数据缺失（2026-08-31 生产故障根因）。
PROFILE_FILES = sorted(Path("/tmp").glob("lc_p*.json"))
INDIVIDUAL = Path("/tmp/low_chip_individual.json")
QUALITY = Path("/tmp/low_chip_quality.json")
UNLOCK = Path("/tmp/low_chip_unlock.json")
ENRICHMENT_STATUS = Path("/tmp/low_chip_enrichment_status.json")

QUALITY_TERMS = (
    "全国社保基金", "社保基金", "基本养老保险基金", "国家集成电路产业投资基金",
    "国新投资", "深圳市创新投资集团", "科威特政府投资局", "澳门金融管理局",
)
INSTITUTIONAL_TERMS = (
    "基金", "保险", "私募", "QFII", "合格境外", "香港中央结算",
    "产业投资", "产业资本", "投资有限公司",
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def industry_text(value) -> str:
    if isinstance(value, list):
        return "--".join(str(item) for item in value if item)
    return str(value or "")


SHAREHOLDER_PERIOD_RE = re.compile(r"^前十大流通股东名称(?:\(报告期\))?\[(\d{8})\]$")


def shareholder_period(key: str) -> str:
    match = SHAREHOLDER_PERIOD_RE.match(key)
    return match.group(1) if match else ""


def latest_shareholder_field(row: dict):
    candidates = [
        (shareholder_period(key), key, value)
        for key, value in row.items()
        if value and shareholder_period(key)
    ]
    if candidates:
        _, key, value = max(candidates, key=lambda item: item[0])
        return key, value
    raw = row.get("前十大流通股东名称")
    return ("前十大流通股东名称", raw) if raw else (None, None)


def shareholders_from_row(row: dict) -> list[str]:
    _, raw = latest_shareholder_field(row)
    if not raw:
        return []
    if isinstance(raw, list):
        names = [str(item).strip() for item in raw if str(item).strip()]
        return list(dict.fromkeys(names))
    text = str(raw).replace("||", ",").replace("，", ",")
    return list(dict.fromkeys(item.strip() for item in text.split(",") if item.strip()))


def merge_profile_rows(batch: dict | None, individual: dict | None) -> dict:
    merged = dict(batch or {})
    current = dict(individual or {})
    if current:
        availability = {
            **(merged.get("_enrichment_availability") or {}),
            **(current.get("_enrichment_availability") or {}),
        }
        merged.update({key: value for key, value in current.items() if value not in (None, "", [])})
        if availability:
            merged["_enrichment_availability"] = availability
        current_key, current_value = latest_shareholder_field(current)
        if current_key and current_value:
            for key in list(merged):
                if key == "前十大流通股东名称" or shareholder_period(key):
                    merged.pop(key, None)
            merged[current_key] = current_value
    return merged


def classify_shareholders(names: list[str]) -> tuple[list[str], list[str]]:
    quality = [name for name in names if any(term in name for term in QUALITY_TERMS)]
    institutional = [
        name for name in names
        if name not in quality and any(term in name for term in INSTITUTIONAL_TERMS)
    ]
    return quality, institutional


def main() -> None:
    payload = load(DATA)
    # The raw intersection (before any filtering) is preserved in intersection_before_filters
    original = list(payload.get("intersection_before_filters") or [])
    excluded_bj = [symbol for symbol in original if symbol.endswith(".BJ")]
    filtered = [symbol for symbol in original if not symbol.endswith(".BJ")]

    profile: dict[str, dict] = {}
    for path in PROFILE_FILES:
        if not path.exists():
            continue
        for row in load(path).get("datas", []):
            code = str(row.get("股票代码") or "")
            if code and code not in profile:
                profile[code] = row
    if INDIVIDUAL.exists():
        for row in load(INDIVIDUAL):
            code = str(row.get("股票代码") or "")
            if code:
                profile[code] = merge_profile_rows(profile.get(code), row)


    unlocks: dict[str, dict] = {}
    cache_path = os.environ.get("LOW_CHIP_UNLOCK_CACHE")
    run_id = os.environ.get("LOW_CHIP_RUN_ID")
    if not cache_path or not run_id:
        blocked("current run id and cache path required at consumer")
    try:
        cache = load(Path(cache_path))
    except (OSError, ValueError) as exc:
        blocked(f"cache unavailable: {exc}")
    for row in validate_cache(cache, payload, run_id):
        code, free_date = row["股票代码"], row["变动日期"]
        batch = {
            "date": f"{free_date[:4]}-{free_date[4:6]}-{free_date[6:8]}",
            "type": row["股份来源"], "ratio": row["占总股本比例"],
            "float_ratio": row["占流通股比例"],
        }
        if code not in unlocks:
            unlocks[code] = {**batch, "batches": []}
        unlocks[code]["batches"].append(batch)

    rows_by_symbol = {}
    for period_rows in payload.get("periods", {}).values():
        for row in period_rows:
            rows_by_symbol.setdefault(row["symbol"], row)

    enrichments = {}
    for code in filtered:
        row = profile.get(code, {})
        industry = industry_text(row.get("所属申万行业"))
        levels = [item for item in industry.split("--") if item]
        sector = levels[-1] if levels else "待补充"
        shareholders = shareholders_from_row(row)
        availability = row.get("_enrichment_availability") or {}
        availability = {
            field: "AVAILABLE" if evidence and availability.get(field) != "UNAVAILABLE" else "UNAVAILABLE"
            for field, evidence in (("industry", industry), ("top10_shareholders", shareholders))
        }
        shareholders_available = availability["top10_shareholders"] == "AVAILABLE"
        quality, institutional = classify_shareholders(shareholders if shareholders_available else [])
        unlock = unlocks.get(code)
        enrichments[code] = {
            "industry": industry or "待补充",
            "sector": sector,
            "enrichment_availability": availability,
            "enrichment_status": "degraded" if "UNAVAILABLE" in availability.values() else "ok",
            "quality_shareholder": bool(quality) if shareholders_available else None,
            "quality_shareholder_names": quality,
            "institutional_shareholder": bool(institutional) if shareholders_available else None,
            "institutional_shareholder_names": institutional,
            "unlock_risk": bool(unlock),
            "unlock": unlock,
        }

    payload["schema_version"] = "a-low-profit-v3"
    payload["universe"] = "沪深A股，非ST，非退市，不含北交所"
    payload["intersection_before_filters"] = original
    payload["intersection"] = [code for code in filtered if code not in unlocks]
    payload["filters"] = {
        **payload.get("filters", {}),
        "exclude_bj": True,
        "excluded_bj": excluded_bj,
        "listing_min_days": payload.get("filters", {}).get("listing_min_days", 90),
        "listing_cutoff": payload.get("filters", {}).get("listing_cutoff", ""),
        "exclude_new_listing": True,
        "excluded_new_listing": payload.get("filters", {}).get("excluded_new_listing", []),
        "unlock_window": "未来3个月",
        "exclude_unlock_risk": True,
        "excluded_unlock_risk": [code for code in filtered if code in unlocks],
        "quality_shareholder_definition": "十大流通股东中的社保、基本养老、国家大基金、国新投资、深创投、科威特政府投资局、澳门金融管理局",
        "institutional_shareholder_definition": "十大流通股东中的公募基金、保险资金、阳光私募、QFII/外资机构、香港中央结算及产业资本；与长期资本型优质股东分级展示",
    }
    payload["enrichments"] = enrichments
    unavailable = {
        field: [code for code in filtered if enrichments[code]["enrichment_availability"][field] == "UNAVAILABLE"]
        for field in ("industry", "top10_shareholders")
    }
    source_status = load(ENRICHMENT_STATUS) if ENRICHMENT_STATUS.exists() else {}
    degraded = (
        any(unavailable.values())
        or source_status.get("status") == "degraded"
        or source_status.get("availability") == "UNAVAILABLE"
    )
    payload["enrichment_status"] = {
        **source_status,
        "status": "degraded" if degraded else "ok",
        "availability": "UNAVAILABLE" if degraded else "AVAILABLE",
        "total": len(filtered),
        "coverage": {field: len(filtered) - len(codes) for field, codes in unavailable.items()},
        "unavailable": unavailable,
    }
    payload["screened_count"] = len(payload["intersection"])
    DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "before": len(original),
        "excluded_bj": excluded_bj,
        "excluded_unlock": payload["filters"]["excluded_unlock_risk"],
        "after": len(payload["intersection"]),
        "quality_count": sum(enrichments[c]["quality_shareholder"] is True for c in payload["intersection"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
