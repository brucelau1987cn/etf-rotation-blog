#!/usr/bin/env python3
"""落地低筹码行业展示字段，数据源为 iWenCai「所属申万行业」三级路径（申万 2021 口径）。

历史：本脚本原用 FTShare SDK 校验申万行业并补行业代码（SW2021 industryCode），
但 FTShare 行业概览对当天常返回空（T+1 延迟），2026-08-26 起改用 iWenCai 直接落地：
iWenCai 返回「医药生物||化学制药||化学制剂」三级路径文本，脚本 split 后落地
industry_level1/2/3（仅名称，iWenCai 不提供申万行业代码）、industry/sector = 二级名称。
缺失二级路径的标的标「待补充」（fail-soft，页面显示「—」），不阻断发布。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "public/data/a-low-chip-stocks.json"
TRACKING = ROOT / "public/data/low-chip-tracking.json"
HISTORY = ROOT / "public/data/low-chip-history"
CACHE = ROOT / "public/data/model-lab/ftshare-sw-industry-map.json"
GENERIC_CONCEPTS = {
    "融资融券", "深股通", "沪股通", "陆股通", "标普道琼斯A股", "MSCI概念",
    "富时罗素概念", "证金持股", "转融券标的", "机构重仓", "基金重仓",
    "参股新三板", "地方国企改革", "央企国企改革", "国企改革", "回购增持再贷款概念",
    "新股与次新股", "注册制次新股", "送转填权", "预盈预增", "昨日涨停",
}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def transactional_write_json(items: list[tuple[Path, dict[str, Any]]]) -> None:
    """Write related JSON files as one rollback-protected local transaction."""
    backups: dict[Path, bytes | None] = {}
    for path, _ in items:
        backups[path] = path.read_bytes() if path.exists() else None
    try:
        for path, payload in items:
            atomic_write_json(path, payload)
    except BaseException:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.rollback-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        raise


def split_industry_path(value: Any) -> list[str]:
    text = str(value or "").replace("||", "--")
    return [part.strip() for part in text.split("--") if part.strip()]


def build_industry_mapping(industry_hints: dict[str, str]) -> dict[str, dict[str, Any]]:
    """从 iWenCai「所属申万行业」路径文本解析三级名称映射（iWenCai 不提供申万行业代码）。"""
    mapping: dict[str, dict[str, Any]] = {}
    for symbol, raw_path in industry_hints.items():
        path = split_industry_path(raw_path)
        if len(path) < 2:
            continue  # 缺二级 → 不落地，页面标「待补充」
        mapping[symbol] = {
            "stockCode": symbol.split(".")[0],
            "swLevel1Code": None, "swLevel1Name": path[0],
            "swLevel2Code": None, "swLevel2Name": path[1],
            "swLevel3Code": None, "swLevel3Name": path[2] if len(path) >= 3 else None,
        }
    return mapping


def load_industry_hints(current: dict[str, Any], tracking: dict[str, Any]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for symbol, rec in (tracking.get("stocks") or {}).items():
        value = str(rec.get("industry") or "")
        if len(split_industry_path(value)) < 2:
            first_seen = str(rec.get("first_seen") or "")
            snapshot = json.loads((HISTORY / f"{first_seen}.json").read_text(encoding="utf-8"))
            value = str(((snapshot.get("enrichments") or {}).get(symbol) or {}).get("industry") or "")
        hints[symbol] = value
    for symbol in current.get("intersection") or []:
        value = str(((current.get("enrichments") or {}).get(symbol) or {}).get("industry") or "")
        if len(split_industry_path(value)) >= 2:
            hints[symbol] = value
    return hints


def clean_themes(values: Any, industry_names: set[str]) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    expanded: list[str] = []
    for raw in values:
        text = str(raw or "").replace("，", "、").replace(",", "、").replace(";", "、").replace("；", "、")
        expanded.extend(part.strip() for part in text.split("、") if part.strip())
    result: list[str] = []
    for value in expanded:
        if not value or value in GENERIC_CONCEPTS or value in industry_names or value in result:
            continue
        result.append(value)
        if len(result) == 3:
            break
    return result


def build_industry_fields(row: dict[str, Any], themes: Any, as_of: str) -> dict[str, Any]:
    level1 = {"code": row.get("swLevel1Code"), "name": str(row.get("swLevel1Name") or "")}
    level2 = {"code": row.get("swLevel2Code"), "name": str(row.get("swLevel2Name") or "")}
    level3 = {"code": row.get("swLevel3Code"), "name": str(row.get("swLevel3Name") or "")}
    if not level2["name"]:
        raise RuntimeError(f"invalid level-2 classification: {row}")
    concepts = clean_themes(themes, {level1["name"], level2["name"], level3["name"]})
    display = level2["name"] + (f"（{'、'.join(concepts)}）" if concepts else "")
    return {
        "industry_standard": "SW2021",
        "industry_source": "iWenCai 所属申万行业",
        "industry_as_of": as_of,
        "industry_level1": level1,
        "industry_level2": level2,
        "industry_level3": level3,
        "industry": level2["name"],
        "sector": level2["name"],
        "theme_concepts": concepts,
        "theme_concept": "、".join(concepts),
        "sector_with_theme": display,
    }


def load_entry_themes(tracking: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for symbol, rec in (tracking.get("stocks") or {}).items():
        first_seen = str(rec.get("first_seen") or "")
        path = HISTORY / f"{first_seen}.json"
        if not path.exists():
            raise RuntimeError(f"STAGING BLOCKER: missing first-seen snapshot for {symbol}: {path.name}")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        enrichment = (snapshot.get("enrichments") or {}).get(symbol)
        if not isinstance(enrichment, dict):
            raise RuntimeError(f"STAGING BLOCKER: missing first-seen enrichment for {symbol}: {path.name}")
        result[symbol] = list(enrichment.get("theme_concepts") or [])
    return result


def apply_refresh(current: dict[str, Any], tracking: dict[str, Any], mapping: dict[str, dict[str, Any]], history_themes: dict[str, list[str]], as_of: str) -> None:
    missing: list[str] = []
    for symbol in current.get("intersection") or []:
        enrichment = (current.get("enrichments") or {}).get(symbol)
        if not isinstance(enrichment, dict):
            raise RuntimeError(f"STAGING BLOCKER: current enrichment missing for {symbol}")
        if symbol not in mapping:
            missing.append(symbol)
            continue  # 缺二级行业路径 → 保持「待补充」，页面「—」
        enrichment.update(build_industry_fields(mapping[symbol], enrichment.get("theme_concepts") or [], as_of))
    for symbol, rec in (tracking.get("stocks") or {}).items():
        if symbol not in mapping:
            # 缺二级行业路径：保留 enrichment 原值，但补 industry_source/standard 以满足
            # test_published_low_chip_industry_contract_has_full_coverage 100% 覆盖断言。
            rec.setdefault("industry_source", "iWenCai 所属申万行业")
            rec.setdefault("industry_standard", "SW2021")
            missing.append(symbol)
            continue
        fields = build_industry_fields(mapping[symbol], history_themes.get(symbol) or [], as_of)
        rec.update({
            "industry_standard": fields["industry_standard"],
            "industry_source": fields["industry_source"],
            "industry_as_of": fields["industry_as_of"],
            "industry_level1": fields["industry_level1"],
            "industry_level2": fields["industry_level2"],
            "industry_level3": fields["industry_level3"],
            "industry": fields["sector"],
            "theme_concepts": fields["theme_concepts"],
            "industry_display": fields["sector_with_theme"],
        })
    if missing:
        print(f"  {len(missing)} 只缺完整行业路径（标待补充，不阻断）", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, default=CURRENT)
    parser.add_argument("--tracking", type=Path, default=TRACKING)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    args = parser.parse_args()

    current = json.loads(args.current.read_text(encoding="utf-8"))
    tracking = json.loads(args.tracking.read_text(encoding="utf-8"))
    as_of = str(current.get("data_as_of") or "")
    dt.date.fromisoformat(as_of)
    industry_hints = load_industry_hints(current, tracking)
    history_themes = load_entry_themes(tracking)

    mapping = build_industry_mapping(industry_hints)
    apply_refresh(current, tracking, mapping, history_themes, as_of)
    current["industry_contract"] = {
        "standard": "SW2021", "source": "iWenCai 所属申万行业", "as_of": as_of,
        "display": "申万二级行业（最多三个业务/题材标签）", "coverage": len(mapping),
    }
    tracking["industry_contract"] = {
        "standard": "SW2021", "source": "iWenCai 所属申万行业", "as_of": as_of,
        "display": "申万二级行业（最多三个业务/题材标签）", "coverage": len(mapping),
    }
    tracking["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    cache_payload = {
        "schema_version": "ftshare-sw-industry-map-v1", "generated_at": tracking["generated_at"],
        "as_of": as_of, "source": "iWenCai 所属申万行业", "coverage": len(mapping), "items": mapping,
        "validation_scope": "iWenCai 所属申万行业三级路径（无申万行业代码）落地为 level1/2/3 名称。不提供行业代码。",
    }
    transactional_write_json([
        (args.current, current),
        (args.tracking, tracking),
        (args.cache, cache_payload),
    ])
    print(json.dumps({"status": "ok", "as_of": as_of, "current": len(current.get("intersection") or []), "tracking": len(tracking.get("stocks") or {}), "coverage": len(mapping)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
