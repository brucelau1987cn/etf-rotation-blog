#!/usr/bin/env python3
"""Update continuous daily tracking for weekly A/US stock recommendations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/weekly-stock-recommendations.json"
CN = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def fetch_a_quote(symbol: str) -> dict:
    prefix = "sh" if symbol.startswith("6") else "sz"
    request = urllib.request.Request(
        f"https://qt.gtimg.cn/q={prefix}{symbol}",
        headers={"Referer": "https://finance.qq.com/", "User-Agent": "Mozilla/5.0"},
    )
    text = urllib.request.urlopen(request, timeout=20).read().decode("gbk", "ignore")
    match = re.search(r'="([^\"]+)"', text)
    if not match:
        raise RuntimeError(f"Tencent quote missing for {symbol}")
    fields = match.group(1).split("~")
    close = _finite(fields[3] if len(fields) > 3 else None)
    previous = _finite(fields[4] if len(fields) > 4 else None)
    date_text = str(fields[30] if len(fields) > 30 else "")[:8]
    if not close or not re.fullmatch(r"\d{8}", date_text):
        raise RuntimeError(f"Tencent quote invalid for {symbol}")
    return {
        "date": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}",
        "close": round(close, 4),
        "change_pct": round((close / previous - 1) * 100, 2) if previous else None,
        "source": "Tencent quote",
    }


def fetch_us_quote(symbol: str) -> dict:
    params = urllib.parse.urlencode({"range": "5d", "interval": "1d", "includePrePost": "false", "events": "div,splits"})
    request = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.load(response)["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    valid = [(ts, _finite(close)) for ts, close in zip(timestamps, closes) if _finite(close)]
    if not valid:
        raise RuntimeError(f"Yahoo quote missing for {symbol}")
    ts, close_value = valid[-1]
    if close_value is None:
        raise RuntimeError(f"Yahoo quote invalid for {symbol}")
    close = float(close_value)
    previous = valid[-2][1] if len(valid) > 1 else None
    day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(NY).date().isoformat()
    return {
        "date": day,
        "close": round(close, 4),
        "change_pct": round((close / previous - 1) * 100, 2) if previous else None,
        "source": "Yahoo Finance chart",
    }


def update_item(item: dict, quote: dict) -> None:
    daily = item.setdefault("daily", [])
    by_date = {row["date"]: row for row in daily if isinstance(row, dict) and row.get("date")}
    baseline = item.get("baseline_close")
    if not baseline:
        baseline = quote["close"]
        item["baseline_close"] = baseline
        item["baseline_date"] = quote["date"]
    quote["return_since_added_pct"] = round((quote["close"] / baseline - 1) * 100, 2) if baseline else None
    by_date[quote["date"]] = quote
    item["daily"] = [by_date[day] for day in sorted(by_date)]
    item["latest"] = quote


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--market", choices=["A", "US", "all"], default="all")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    errors = []
    for market in ("A", "US"):
        if args.market not in ("all", market):
            continue
        fetch = fetch_a_quote if market == "A" else fetch_us_quote
        for item in payload["markets"][market]["items"]:
            try:
                update_item(item, fetch(item["symbol"]))
            except Exception as exc:
                errors.append(f"{market} {item['symbol']}: {exc}")
    payload["generated_at"] = dt.datetime.now(CN).isoformat(timespec="seconds")
    payload["status"] = "ok" if not errors else "partial"
    payload["errors"] = errors
    atomic_write_json(args.input, payload)
    print(json.dumps({"status": payload["status"], "errors": errors, "counts": {m: len(payload["markets"][m]["items"]) for m in ("A", "US")}}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
