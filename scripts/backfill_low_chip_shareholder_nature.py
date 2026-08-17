#!/usr/bin/env python3
"""Backfill historical low-chip shareholder nature from dated top-10 evidence."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "public/data/low-chip-history"
CURRENT = ROOT / "public/data/a-low-chip-stocks.json"
CACHE = Path("/tmp/low-chip-shareholder-nature-cache.json")
PERIOD_RE = re.compile(r"^(20\d{6})$")

QUALITY_TERMS = (
    "全国社保基金", "社保基金", "基本养老保险基金", "国家集成电路产业投资基金",
    "国新投资", "深圳市创新投资集团", "科威特政府投资局", "澳门金融管理局",
)
INSTITUTIONAL_TERMS = (
    "基金", "保险", "私募", "QFII", "合格境外", "香港中央结算",
    "HKSCC", "产业投资", "产业资本", "投资有限公司",
)


def normalize_period(value: object) -> str:
    token = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    return token if PERIOD_RE.match(token) else ""


def select_holder_period(rows: list[dict], snapshot_date: str) -> str:
    cutoff = normalize_period(snapshot_date)
    periods = sorted({normalize_period(row.get("period")) for row in rows} - {""})
    eligible = [period for period in periods if not cutoff or period <= cutoff]
    return eligible[-1] if eligible else ""


def select_evidence_period(rows: list[dict], preferred_period: str, snapshot_date: str) -> str:
    available = {
        normalize_period(row.get("period"))
        for row in rows
        if normalize_period(row.get("period")) and str(row.get("name") or "").strip()
    }
    preferred = normalize_period(preferred_period)
    if preferred in available:
        return preferred
    return select_holder_period(
        [row for row in rows if normalize_period(row.get("period")) in available],
        snapshot_date,
    )


def classify_holder_names(names: list[str]) -> tuple[list[str], list[str]]:
    deduped = list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
    quality = [name for name in deduped if any(term in name for term in QUALITY_TERMS)]
    institutional = [
        name for name in deduped
        if name not in quality and any(term in name for term in INSTITUTIONAL_TERMS)
    ]
    return quality, institutional


def apply_nature(payload: dict, evidence: dict[str, list[dict]]) -> list[str]:
    missing = []
    enrichments = payload.setdefault("enrichments", {})
    snapshot_date = str(payload.get("data_as_of") or "")
    for code in payload.get("intersection") or []:
        rows = evidence.get(code) or []
        enr = enrichments.setdefault(code, {})
        sm = enr.get("shareholder_metrics") or {}
        preferred_period = enr.get("shareholder_nature_report_period") or sm.get("report_period")
        period = select_evidence_period(rows, str(preferred_period or ""), snapshot_date)
        names = [row.get("name") for row in rows if normalize_period(row.get("period")) == period]
        names = list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
        if not period or not names:
            missing.append(code)
            continue
        quality, institutional = classify_holder_names(names)
        enr["shareholder_nature_report_period"] = period
        enr["quality_shareholder"] = bool(quality)
        enr["quality_shareholder_names"] = quality
        enr["institutional_shareholder"] = bool(institutional)
        enr["institutional_shareholder_names"] = institutional
    return missing


def iwencai(query: str, limit: int = 30, timeout: int = 90) -> dict:
    result = subprocess.run(
        ["/root/.hermes/scripts/iwencai-market-query", "-q", query,
         "--limit", str(limit), "--timeout", str(timeout)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"iWenCai query failed: {result.stderr[:300]}")
    return json.loads(result.stdout)


def report_periods_for_symbol(code: str) -> list[str]:
    bare = code.split(".")[0]
    data = iwencai(f"{bare} 前十大流通股东报告期、前十大流通股东名称", limit=50)
    periods = set()
    for row in data.get("datas") or []:
        for key in row:
            if key.startswith("前十大流通股东名称") and "[" in key and key.endswith("]"):
                period = normalize_period(key.rsplit("[", 1)[1][:-1])
                if period:
                    periods.add(period)
        period = normalize_period(row.get("报告期") or row.get("截止日期"))
        if period:
            periods.add(period)
    return sorted(periods)


def holder_names_for_period(code: str, period: str) -> list[str]:
    bare = code.split(".")[0]
    quarter = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}.get(period[4:])
    query = (
        f"{bare} {period[:4]}年{quarter}前十大流通股东明细、流通股东名称"
        if quarter else f"{bare} 前十大流通股东名称[{period}]"
    )
    data = iwencai(query, limit=30)
    names = []
    for row in data.get("datas") or []:
        row_code = str(row.get("股票代码") or "").split(".")[0]
        if row_code and row_code != bare:
            continue
        single = str(row.get("流通股东名称") or "").strip()
        if single:
            names.append(single)
        for key, value in row.items():
            if key.startswith("前十大流通股东名称") and value:
                text = str(value).replace("||", ",").replace("，", ",")
                names.extend(item.strip() for item in text.split(",") if item.strip())
    return list(dict.fromkeys(names))


def required_periods(payloads: list[tuple[Path, dict]]) -> dict[str, set[str]]:
    required: dict[str, set[str]] = defaultdict(set)
    for _, payload in payloads:
        for code in payload.get("intersection") or []:
            enr = (payload.get("enrichments") or {}).get(code) or {}
            sm = enr.get("shareholder_metrics") or {}
            period = normalize_period(
                enr.get("shareholder_nature_report_period") or sm.get("report_period")
            )
            if period:
                required[code].add(period)
    return required


def collect_evidence(required: dict[str, set[str]], refresh: bool = False) -> dict[str, list[dict]]:
    cache = {}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    changed = False
    for code, periods in sorted(required.items()):
        existing = cache.get(code) or []
        cached_periods = {normalize_period(row.get("period")) for row in existing}
        rows = list(existing)
        for period in sorted(periods):
            if period in cached_periods and not refresh:
                continue
            names = holder_names_for_period(code, period)
            rows = [row for row in rows if normalize_period(row.get("period")) != period]
            rows.extend({"period": period, "name": name} for name in names)
            changed = True
            print(f"{code} {period}: names={len(names)}", flush=True)
        if not any(str(row.get("name") or "").strip() for row in rows):
            candidate_periods = set(report_periods_for_symbol(code))
            for period in periods:
                year = period[:4]
                for suffix in ("0331", "0630", "0930", "1231"):
                    candidate = year + suffix
                    if candidate < period:
                        candidate_periods.add(candidate)
            for period in sorted(candidate_periods, reverse=True):
                if period in periods:
                    continue
                names = holder_names_for_period(code, period)
                rows.extend({"period": period, "name": name} for name in names)
                changed = True
                print(f"{code} fallback {period}: names={len(names)}", flush=True)
                if names:
                    break
        cache[code] = rows
    if changed:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    paths = sorted(HISTORY_DIR.glob("????-??-??.json"))
    payloads = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    if CURRENT.exists():
        payloads.append((CURRENT, json.loads(CURRENT.read_text(encoding="utf-8"))))
    required = required_periods(payloads)
    evidence = collect_evidence(required, refresh=args.refresh)

    blockers = {}
    for path, payload in payloads:
        missing = apply_nature(payload, evidence)
        if missing:
            blockers[path.name] = missing
            continue
        if args.write:
            path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(payload.get('intersection') or [])} classified", flush=True)
    if blockers:
        print(json.dumps({"status": "STAGING BLOCKER", "missing": blockers}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
