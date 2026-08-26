#!/usr/bin/env python3
"""Refresh low-chip industry display from FTShare SW2021 classification."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "public/data/a-low-chip-stocks.json"
TRACKING = ROOT / "public/data/low-chip-tracking.json"
HISTORY = ROOT / "public/data/low-chip-history"
CACHE = ROOT / "public/data/model-lab/ftshare-sw-industry-map.json"
SDK_BASE_URL = "https://market.ft.tech/gateway/"
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


def symbol_with_exchange(stock_code: Any) -> str:
    code = str(stock_code or "").split(".")[0].zfill(6)
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def active_on(row: dict[str, Any], as_of: str) -> bool:
    in_date = str(row.get("inDate") or "")[:10]
    out_date = str(row.get("outDate") or "")[:10]
    return (not in_date or in_date <= as_of) and (not out_date or out_date > as_of)


def unwrap_overview(payload: Any) -> list[dict[str, Any]]:
    pages = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("code") not in (0, 200, "0", "200", None):
            raise RuntimeError(f"FTShare overview error: {page}")
        data = page.get("data") or {}
        page_rows = data.get("records") or data.get("items") or []
        if not isinstance(page_rows, list):
            raise RuntimeError("FTShare overview returned invalid rows")
        rows.extend(row for row in page_rows if isinstance(row, dict))
    return rows


def resolve_overview_date(client: Any, as_of: str, max_back: int = 15) -> str:
    """FTShare 申万行业概览对当天可能未就绪（total=0）。

    沿自然日向前回溯（周末/节假日返回 0 自然跳过），返回最近一个 level-1 有数据的日期。
    该日期即为行业分类实际生效日期，持久化到 industry_as_of，而非 data_as_of。
    """
    start = dt.date.fromisoformat(as_of)
    for offset in range(max_back + 1):
        candidate = start - dt.timedelta(days=offset)
        rows = unwrap_overview(client.sw_industry_overview(
            date=candidate.strftime("%Y%m%d"), level=1, all_pages=True,
            page_size=200, max_pages=5, raw=True,
        ))
        if rows:
            return candidate.isoformat()
    raise RuntimeError(f"FTShare SW level-1 overview empty for {as_of} and {max_back} days back")


def unwrap_constituents(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("code") not in (0, 200, "0", "200", None):
        raise RuntimeError(f"FTShare constituent error: {payload}")
    data = payload.get("data") or {}
    rows = data.get("items") or data.get("records") or []
    if not isinstance(rows, list):
        raise RuntimeError("FTShare constituent returned invalid rows")
    return [row for row in rows if isinstance(row, dict)]


def merge_classification(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return dict(candidate)
    existing_score = sum(bool(existing.get(key)) for key in ("swLevel1Code", "swLevel2Code", "swLevel3Code"))
    candidate_score = sum(bool(candidate.get(key)) for key in ("swLevel1Code", "swLevel2Code", "swLevel3Code"))
    return dict(candidate if candidate_score >= existing_score else existing)


def split_industry_path(value: Any) -> list[str]:
    text = str(value or "").replace("||", "--")
    return [part.strip() for part in text.split("--") if part.strip()]


def _prev_trading_day(as_of: str) -> str:
    """Return the previous trading day (Mon–Fri) as YYYY-MM-DD."""
    today = dt.date.fromisoformat(as_of)
    wd = today.weekday()
    if wd == 0:  # Monday → Friday (yesterday)
        prev = today - dt.timedelta(days=1)
    elif wd == 6:  # Sunday → Friday
        prev = today - dt.timedelta(days=2)
    else:  # Tue–Sat → yesterday
        prev = today - dt.timedelta(days=1)
    return prev.isoformat()


def fetch_industry_map(client: Any, as_of: str, industry_hints: dict[str, str]) -> tuple[dict[str, dict[str, Any]], str]:
    """Returns (symbol→mapping dict, effective_as_of). effective_as_of may differ from input when upstream lags."""
    overviews: list[dict[str, Any]] = []
    effective_as_of = as_of
    for level, max_pages in ((1, 5), (2, 10), (3, 10)):
        rows = unwrap_overview(client.sw_industry_overview(
            date=as_of.replace("-", ""), level=level, all_pages=True,
            page_size=200, max_pages=max_pages, raw=True,
        ))
        if not rows:
            if level == 1:
                prev = _prev_trading_day(as_of)
                print(f"⚠️ FTShare SW level-1 empty for {as_of}, falling back to {prev}", flush=True)
                rows = unwrap_overview(client.sw_industry_overview(
                    date=prev.replace("-", ""), level=level, all_pages=True,
                    page_size=200, max_pages=max_pages, raw=True,
                ))
                if rows:
                    effective_as_of = prev
            if not rows:
                raise RuntimeError(f"FTShare SW level-{level} overview empty for {effective_as_of}")
        overviews.extend(rows)
    if effective_as_of != as_of:
        print(f"⚠️ FTShare industry effective_as_of = {effective_as_of} (upstream lag)", flush=True)

    by_name = {str(row.get("industryName") or ""): row for row in overviews}
    mapping: dict[str, dict[str, Any]] = {}
    for symbol, raw_path in industry_hints.items():
        path = split_industry_path(raw_path)
        if len(path) < 2:
            continue
        level1 = by_name.get(path[0])
        level2 = by_name.get(path[1])
        level3 = by_name.get(path[2]) if len(path) >= 3 else None
        if not level1 or not level2:
            continue
        if int(level1.get("level") or 0) != 1 or int(level2.get("level") or 0) != 2:
            continue
        if str(level2.get("parentIndustryName") or "") != path[0]:
            continue
        if level3 and (
            int(level3.get("level") or 0) != 3
            or str(level3.get("parentIndustryName") or "") != path[1]
        ):
            continue
        mapping[symbol] = {
            "stockCode": symbol.split(".")[0],
            "swLevel1Code": level1.get("industryCode"), "swLevel1Name": path[0],
            "swLevel2Code": level2.get("industryCode"), "swLevel2Name": path[1],
            "swLevel3Code": level3.get("industryCode") if level3 else None,
            "swLevel3Name": path[2] if level3 else None,
        }
    missing = sorted(set(industry_hints) - mapping.keys())
    if missing:
        raise RuntimeError(f"STAGING BLOCKER: FTShare SW industry missing {len(missing)} symbols: {missing}")
    return mapping, effective_as_of


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
    level1 = {"code": str(row.get("swLevel1Code") or ""), "name": str(row.get("swLevel1Name") or "")}
    level2 = {"code": str(row.get("swLevel2Code") or ""), "name": str(row.get("swLevel2Name") or "")}
    level3 = {"code": str(row.get("swLevel3Code") or ""), "name": str(row.get("swLevel3Name") or "")}
    if not level2["code"] or not level2["name"]:
        raise RuntimeError(f"invalid FTShare level-2 classification: {row}")
    concepts = clean_themes(themes, {level1["name"], level2["name"], level3["name"]})
    display = level2["name"] + (f"（{'、'.join(concepts)}）" if concepts else "")
    return {
        "industry_standard": "SW2021",
        "industry_source": "FTShare Python SDK",
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
    for symbol in current.get("intersection") or []:
        enrichment = (current.get("enrichments") or {}).get(symbol)
        if not isinstance(enrichment, dict):
            raise RuntimeError(f"STAGING BLOCKER: current enrichment missing for {symbol}")
        enrichment.update(build_industry_fields(mapping[symbol], enrichment.get("theme_concepts") or [], as_of))
    for symbol, rec in (tracking.get("stocks") or {}).items():
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
    required = set(current.get("intersection") or []) | set((tracking.get("stocks") or {}).keys())
    industry_hints = load_industry_hints(current, tracking)
    if set(industry_hints) != required:
        raise RuntimeError(f"STAGING BLOCKER: missing industry hints for {sorted(required - set(industry_hints))}")
    history_themes = load_entry_themes(tracking)

    from ftshare.client import FtshareClient
    client = FtshareClient(base_url=SDK_BASE_URL, timeout=60)
    try:
        mapping, effective_as_of = fetch_industry_map(client, as_of, industry_hints)
    finally:
        client.close()

    apply_refresh(current, tracking, mapping, history_themes, effective_as_of)
    current["industry_contract"] = {
        "standard": "SW2021", "source": "FTShare Python SDK", "as_of": effective_as_of,
        "display": "申万二级行业（最多三个业务/题材标签）", "coverage": len(current.get("intersection") or []),
    }
    tracking["industry_contract"] = {
        "standard": "SW2021", "source": "FTShare Python SDK", "as_of": effective_as_of,
        "display": "申万二级行业（最多三个业务/题材标签）", "coverage": len(tracking.get("stocks") or {}),
    }
    tracking["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    cache_payload = {
        "schema_version": "ftshare-sw-industry-map-v1", "generated_at": tracking["generated_at"],
        "as_of": effective_as_of, "source": "FTShare Python SDK", "coverage": len(mapping), "items": mapping,
        "validation_scope": "FTShare SW2021 overview validates hierarchy names, parent relationships, and codes for every mapped stock. It does not assert complete per-stock constituent-history coverage.",
    }
    transactional_write_json([
        (args.current, current),
        (args.tracking, tracking),
        (args.cache, cache_payload),
    ])
    print(json.dumps({"status": "ok", "as_of": effective_as_of, "current": len(current.get("intersection") or []), "tracking": len(tracking.get("stocks") or {}), "coverage": len(mapping)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
