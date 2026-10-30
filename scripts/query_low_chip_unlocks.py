#!/usr/bin/env python3
"""Query one complete calendar-month unlock window; ambiguous coverage blocks staging."""
from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "https://data.eastmoney.com/"
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
LIFT_COLUMNS = "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES_TYPE,TOTAL_RATIO,FREE_RATIO"


def window_end(start: date) -> date:
    month = start.month + 2
    year, month = start.year + month // 12, month % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def blocked(reason: str) -> NoReturn:
    raise ValueError(f"STAGING BLOCKER: unlock {reason}")


def unlock_query_codes(payload: dict) -> list[str]:
    """Cover the full pre-filter pool, including stocks that later gates restore."""
    raw = payload.get("intersection_before_filters")
    candidates = raw if isinstance(raw, list) else (payload.get("intersection") or [])
    result = []
    for value in candidates:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?", value):
            blocked("invalid requested stock code")
        symbol = value
        if symbol.endswith(".BJ") or symbol in result:
            continue
        result.append(symbol)
    return result


def ratio(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        blocked("unknown ratio")
    try:
        number = float(value)
    except (TypeError, ValueError):
        blocked("unknown ratio")
    if not math.isfinite(number) or number < 0:
        blocked("invalid ratio")
    return number


def provider_rows(response: dict, start: date, end: date, codes: list[str]) -> list[dict]:
    """One response contains every row: no paging drift or tied-key inference."""
    if not isinstance(response, dict) or response.get("success") is not True:
        blocked("provider success missing")
    result = response.get("result")
    if not isinstance(result, dict):
        blocked("provider result missing")
    rows, count, pages = result.get("data"), result.get("count"), result.get("pages")
    if (not isinstance(rows, list) or type(count) is not int or type(pages) is not int
            or not 0 < count <= 500 or count != len(rows) or pages != 1):
        blocked("incomplete or ambiguous provider coverage")
    by_code = {code.split(".")[0]: code for code in codes}
    selected = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("SECURITY_CODE"), str)
                or not re.fullmatch(r"\d{6}", row["SECURITY_CODE"])):
            blocked("invalid provider stock code")
        raw_date = row.get("FREE_DATE")
        try:
            if not isinstance(raw_date, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
                r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?)?", raw_date
            ):
                blocked("invalid provider date")
            free_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            blocked("invalid provider date")
        if free_date is None or not start <= free_date <= end:
            blocked("provider date outside requested window")
        total, floating = ratio(row.get("TOTAL_RATIO")), ratio(row.get("FREE_RATIO"))
        symbol = by_code.get(row["SECURITY_CODE"])
        if symbol:
            selected.append({
                "股票代码": symbol, "变动日期": free_date.strftime("%Y%m%d"),
                "股份来源": str(row.get("FREE_SHARES_TYPE") or "限售股"),
                "占总股本比例": total, "占流通股比例": floating,
            })
    return selected


def build_cache(payload: dict, run_id: str) -> dict:
    if not isinstance(run_id, str) or not run_id.strip():
        blocked("current run id required")
    try:
        start = date.fromisoformat(payload["data_as_of"])
    except (KeyError, TypeError, ValueError):
        blocked("valid data_as_of required")
    end, codes = window_end(start), unlock_query_codes(payload)
    params = {
        "sortColumns": "FREE_DATE,SECURITY_CODE", "sortTypes": "1,1",
        "pageSize": "500", "pageNumber": "1", "reportName": "RPT_LIFT_STAGE",
        "filter": f"(FREE_DATE>='{start.isoformat()}')(FREE_DATE<='{end.isoformat()}')",
        "columns": LIFT_COLUMNS, "source": "WEB", "client": "WEB",
    }
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": UA, "Referer": REFERER})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = json.loads(response.read().decode())
    rows = provider_rows(raw, start, end, codes)
    return {
        "status": "complete", "run_id": run_id, "data_as_of": start.isoformat(),
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "requested_codes": codes, "provider_response": raw, "request": params,
        "observed_at": datetime.now(CN).isoformat(), "datas": rows,
    }


def validate_cache(cache: dict, payload: dict, run_id: str) -> list[dict]:
    if not isinstance(cache, dict) or not run_id or cache.get("run_id") != run_id:
        blocked("cache run id mismatch")
    try:
        start = date.fromisoformat(payload["data_as_of"])
    except (KeyError, TypeError, ValueError):
        blocked("valid data_as_of required at consumer")
    end, codes = window_end(start), unlock_query_codes(payload)
    expected = {
        "status": "complete", "data_as_of": start.isoformat(),
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "requested_codes": codes,
    }
    if any(cache.get(key) != value for key, value in expected.items()):
        blocked("cache date/window/universe/status mismatch")
    # Rebuild every cached row from the retained complete response. This also
    # rejects missing batches and unknown ratios before any membership changes.
    rows = provider_rows(cache.get("provider_response"), start, end, codes)
    if cache.get("datas") != rows:
        blocked("cached rows differ from complete provider evidence")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=os.environ.get("LOW_CHIP_UNLOCK_CACHE"))
    parser.add_argument("--run-id", default=os.environ.get("LOW_CHIP_RUN_ID"))
    args = parser.parse_args()
    if not args.output or not args.run_id:
        blocked("run-specific output path and run id required")
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    result = build_cache(payload, args.run_id)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    temporary.replace(out)
    print(json.dumps({"status": "ok", "window": [result["window_start"], result["window_end"]],
                      "unlock_rows": len(result["datas"]), "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
