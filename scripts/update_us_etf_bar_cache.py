#!/usr/bin/env python3
"""Refresh the local US ETF daily-bar cache from Yahoo Chart API."""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 ETF-Compass-US-Cache/1.0"


def load_garden_module():
    path = ROOT / "scripts" / "generate_us_etf_garden.py"
    spec = importlib.util.spec_from_file_location("generate_us_etf_garden", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cache_module():
    path = ROOT / "scripts" / "us_etf_bar_cache.py"
    spec = importlib.util.spec_from_file_location("us_etf_bar_cache", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def session_state(now: datetime) -> str:
    minutes = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return "closed"
    if minutes < 9 * 60 + 30:
        return "preopen"
    if minutes >= 16 * 60 + 5:
        return "closed"
    return "open"


def fetch_yahoo(symbol: str, range_: str = "2y") -> list[dict[str, Any]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval=1d&events=div%2Csplits"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.load(response)["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    rows = []
    for i, ts in enumerate(result["timestamp"]):
        vals = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
        adj = adjusted[i] if i < len(adjusted) else quote["close"][i]
        if any(v is None for v in vals[:4]) or adj is None:
            continue
        trade_date = datetime.fromtimestamp(ts, timezone.utc).astimezone(NY).date().isoformat()
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": float(vals[0]),
                "high": float(vals[1]),
                "low": float(vals[2]),
                "close": float(vals[3]),
                "adj_close": float(adj),
                "volume": float(vals[4] or 0),
                "source": "yahoo",
            }
        )
    if len(rows) < 60:
        raise RuntimeError(f"{symbol}: only {len(rows)} rows")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--range", default="2y", help="Yahoo range, e.g. 3mo / 1y / 2y")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--mark-final", action="store_true", help="Force is_final=1 even during session")
    args = parser.parse_args()

    garden = load_garden_module()
    cache = load_cache_module()
    now = datetime.now(NY)
    state = session_state(now)
    is_final = True if args.mark_final else state == "closed"
    run_id = f"us-cache-{now.strftime('%Y%m%dT%H%M%S')}"
    started = cache.utc_now()
    t0 = time.time()

    instruments = [
        {
            "symbol": symbol,
            "name": name,
            "asset_type": asset_type,
            "theme": theme,
        }
        for symbol, name, asset_type, theme in garden.UNIVERSE
    ]
    symbols = [x["symbol"] for x in instruments]
    results: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch_yahoo, symbol, args.range): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as exc:  # noqa: BLE001
                failures[symbol] = f"{type(exc).__name__}: {exc}"

    bars: list[dict[str, Any]] = []
    for symbol, rows in results.items():
        for row in rows:
            item = dict(row)
            # Incomplete cash-session bar for today stays non-final unless forced.
            if not is_final and item["trade_date"] == now.date().isoformat():
                item["is_final"] = 0
            else:
                item["is_final"] = 1
            bars.append(item)

    with cache.connect() as db:
        cache.upsert_instruments(db, instruments)
        written = cache.upsert_bars(db, bars)
        latest = cache.latest_trade_date(db)
        covered = cache.coverage(db, symbols, latest or "", source="yahoo") if latest else 0
        status = "ok" if len(results) >= int(len(symbols) * 0.9) else "partial"
        cache.audit(
            db,
            run_id=run_id,
            source="yahoo",
            started_at=started,
            requested=len(symbols),
            succeeded=len(results),
            failed=len(failures),
            latency_ms=int((time.time() - t0) * 1000),
            status=status,
            detail={"failures": failures, "session_state": state, "range": args.range},
        )

    print(
        json.dumps(
            {
                "status": status,
                "run_id": run_id,
                "session_state": state,
                "is_final_default": is_final,
                "requested": len(symbols),
                "succeeded": len(results),
                "failed": len(failures),
                "bars_written": written,
                "latest_trade_date": latest,
                "coverage": covered,
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )
    return 0 if status in {"ok", "partial"} and len(results) >= 60 else 1


if __name__ == "__main__":
    raise SystemExit(main())
