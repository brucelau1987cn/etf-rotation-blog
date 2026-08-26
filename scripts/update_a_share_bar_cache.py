#!/usr/bin/env python3
"""Incrementally import A-share ETF qfq bars from iWenCai into SQLite."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib.util
import io
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = Path.home() / ".hermes" / "scripts" / "iwencai-market-query"
RAW_ROOT = ROOT / "data" / "local" / "raw" / "iwencai"
sys.path.insert(0, str(ROOT / "scripts"))
from etf_bar_cache import DEFAULT_DB, audit, connect, upsert_bars, upsert_instruments, utc_now  # noqa: E402

FIELD_RE = re.compile(r"^(?:基金@)?(开盘价|最高价|最低价|收盘价|成交量|成交额)(?:(?:_|:)前复权)?\[(\d{8})\]$")
FIELD_MAP = {"开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume", "成交额": "amount"}
CN = ZoneInfo("Asia/Shanghai")
STOCK_API_PACKAGE = "stock-api@2.7.3"


def valid_ohlc(bar: dict[str, Any]) -> bool:
    try:
        open_, high, low, close = (float(bar[field]) for field in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) and value > 0 for value in (open_, high, low, close))
        and high >= max(open_, close)
        and low <= min(open_, close)
    )


def load_universe() -> list[dict[str, str]]:
    path = ROOT / "scripts" / "generate_garden_pool.py"
    spec = importlib.util.spec_from_file_location("garden_pool", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load A-share ETF universe")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.GARDEN_POOL


def query_batch(items: list[dict[str, str]], days: int, timeout: int = 60) -> dict[str, Any]:
    codes = " ".join(x["code"] for x in items)
    query = f"{codes}近{days}日每天的前复权开盘价最高价最低价收盘价成交量成交额"
    proc = subprocess.run(
        [str(WRAPPER), "--query", query, "--limit", str(max(10, len(items) + 2)), "--timeout", "45"],
        text=True, capture_output=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "iWenCai failed")[:500])
    payload = json.loads(proc.stdout)
    if not payload.get("success", True):
        raise RuntimeError(str(payload.get("message") or payload.get("error") or "iWenCai failed"))
    return payload


def parse_payload(payload: dict[str, Any], item_map: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], set[str]]:
    now = datetime.now(CN)
    today = now.date().isoformat()
    current_is_final = now.hour > 15 or (now.hour == 15 and now.minute >= 15)
    bars_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    symbols: set[str] = set()
    for record in payload.get("datas") or []:
        raw_code = str(record.get("基金代码") or record.get("股票代码") or "")
        symbol = raw_code.split(".")[0]
        item = item_map.get(symbol)
        if not item:
            continue
        symbols.add(symbol)
        market = item["market"]
        for key, value in record.items():
            match = FIELD_RE.match(str(key))
            if not match:
                continue
            field_cn, raw_date = match.groups()
            observed = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            try:
                numeric = float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
            bar = bars_by_key.setdefault((symbol, observed), {
                "market": market, "symbol": symbol, "trade_date": observed,
                "adjustment": "qfq", "source": "iwencai",
                "is_final": observed < today or (observed == today and current_is_final),
            })
            bar[FIELD_MAP[field_cn]] = numeric
    bars = [x for x in bars_by_key.values() if valid_ohlc(x)]
    symbols = {x["symbol"] for x in bars}
    return bars, symbols


def parse_stock_api_rows(payload: Any, item: dict[str, str], now: datetime | None = None, source: str = "stock-api") -> list[dict[str, Any]]:
    current = now or datetime.now(CN)
    today = current.date().isoformat()
    current_is_final = current.hour > 15 or (current.hour == 15 and current.minute >= 15)
    rows = payload if isinstance(payload, list) else (payload.get("klines") or payload.get("data") or payload.get("rows") or [])
    parsed: list[dict[str, Any]] = []
    for row in rows:
        observed = str(row.get("date") or "")[:10]
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if not observed or not math.isfinite(close):
            continue
        bar = {
            "market": item["market"], "symbol": item["code"], "trade_date": observed,
            "close": close, "adjustment": "qfq", "source": source,
            "is_final": observed < today or (observed == today and current_is_final),
        }
        for field in ("open", "high", "low", "volume", "amount"):
            try:
                value = float(row.get(field))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                bar[field] = value
        if valid_ohlc(bar):
            parsed.append(bar)
    return parsed


def fetch_tencent_history(item: dict[str, str], count: int) -> list[dict[str, Any]]:
    symbol = ("sh" if item["market"] == "XSHG" else "sz") + item["code"]
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") or {}
            if not data or data == "":
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))
                    continue
                return []
            node = data.get(symbol) or {}
            raw_rows = node.get("qfqday") or node.get("day") or []
            normalized = [{"date": row[0], "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": row[5]}
                          for row in raw_rows if isinstance(row, list) and len(row) >= 6]
            return parse_stock_api_rows(normalized, item, source="tencent")
        except Exception:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                raise
    return []


def fetch_baostock_history(item: dict[str, str], count: int) -> list[dict[str, Any]]:
    import baostock as bs  # type: ignore[import-not-found]
    market_code = "sh." + item["code"] if item["market"] == "XSHG" else "sz." + item["code"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rs = bs.query_history_k_data_plus(
            market_code,
            "date,code,open,high,low,close,volume,amount",
            start_date=(datetime.now() - timedelta(days=count * 2)).strftime("%Y-%m-%d"),
            end_date="2099-12-31",
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
    if not rows:
        return []
    normalized = [{"date": r[0], "open": r[2], "close": r[5], "high": r[3], "low": r[4], "volume": r[6]}
                  for r in rows if r[0] and r[5] != ""]
    return parse_stock_api_rows(normalized, item, source="baostock")


def fetch_primary_history(item: dict[str, str], count: int) -> tuple[list[dict[str, Any]], str]:
    try:
        rows = fetch_tencent_history(item, count)
        if rows:
            return rows, "tencent"
    except Exception:
        pass
    rows = fetch_baostock_history(item, count)
    return rows, "baostock"


def summarize_source_coverage(bars: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for bar in reversed(bars):
        sym = bar.get("symbol", "")
        src = bar.get("source", "")
        if sym and src and sym not in seen:
            counts[src] = counts.get(src, 0) + 1
            seen.add(sym)
    return counts


def fetch_stock_api_history(item: dict[str, str], count: int) -> list[dict[str, Any]]:
    market_code = ("SH" if item["market"] == "XSHG" else "SZ") + item["code"]
    command = ["npx", "-y", STOCK_API_PACKAGE, "get-klines", market_code,
               "--period", "day", "--count", str(count), "--adjust", "qfq", "--source", "auto"]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError((proc.stderr or proc.stdout or "stock-api failed")[-500:])
    rows = parse_stock_api_rows(json.loads(proc.stdout), item)
    return rows or fetch_tencent_history(item, count)


def symbols_needing_backfill(db: sqlite3.Connection, universe: list[dict[str, str]], minimum: int) -> list[dict[str, str]]:
    counts = {row[0]: int(row[1]) for row in db.execute(
        "SELECT symbol,count(distinct trade_date) FROM daily_bars WHERE adjustment='qfq' AND is_final=1 GROUP BY symbol"
    )}
    return [item for item in universe if counts.get(item["code"], 0) < minimum]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--symbols", help="comma-separated subset for repair runs")
    parser.add_argument("--backfill-days", type=int, default=0, help="stock-api qfq history count for short-history symbols")
    parser.add_argument("--backfill-workers", type=int, default=4)
    parser.add_argument("--minimum-history", type=int, default=260)
    parser.add_argument("--source", choices=["tencent", "iwencai"], default="tencent",
                        help="qfq data source (default: tencent)")
    parser.add_argument("--workers", type=int, default=4, help="parallel workers for tencent source")
    args = parser.parse_args()
    full_universe = load_universe()
    wanted = {x.strip() for x in (args.symbols or "").split(",") if x.strip()}
    universe = [x for x in full_universe if not wanted or x["code"] in wanted]
    item_map = {x["code"]: x for x in universe}
    run_id = f"{args.source}-" + datetime.now(CN).strftime("%Y%m%d-%H%M%S")
    started = utc_now()
    t0 = time.monotonic()
    all_bars: list[dict[str, Any]] = []
    succeeded: set[str] = set()
    source_coverage: dict[str, int] = {}
    errors: list[str] = []

    if args.source == "tencent":
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(fetch_primary_history, item, args.days): item for item in universe}
            for future in concurrent.futures.as_completed(futures):
                item = futures[future]
                try:
                    rows, src = future.result()
                    if not rows:
                        errors.append(f"{item['code']}: no data from {src}")
                        continue
                    all_bars.extend(rows)
                    succeeded.add(item["code"])
                    source_coverage[src] = source_coverage.get(src, 0) + 1
                except Exception as exc:
                    errors.append(f"{item['code']}: {type(exc).__name__}: {exc}")
    else:
        RAW_ROOT.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 90 * 86400
        for old in RAW_ROOT.glob("*.json"):
            if old.stat().st_mtime < cutoff:
                old.unlink()
        for start in range(0, len(universe), args.batch_size):
            batch = universe[start:start + args.batch_size]
            try:
                payload = query_batch(batch, args.days)
                bars, symbols = parse_payload(payload, item_map)
                all_bars.extend(bars); succeeded.update(symbols)
                raw_path = RAW_ROOT / f"{run_id}-{start // args.batch_size + 1:02d}.json"
                raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                errors.append(f"batch {start // args.batch_size + 1}: {type(exc).__name__}: {exc}")
        missing = [x for x in universe if x["code"] not in succeeded]
        for start in range(0, len(missing), 4):
            batch = missing[start:start + 4]
            try:
                payload = query_batch(batch, args.days)
                bars, symbols = parse_payload(payload, item_map)
                all_bars.extend(bars); succeeded.update(symbols)
            except Exception as exc:
                errors.append(f"repair {start // 4 + 1}: {type(exc).__name__}: {exc}")
        remaining = [x for x in universe if x["code"] not in succeeded]
        for index, item in enumerate(remaining, 1):
            try:
                payload = query_batch([item], args.days)
                bars, symbols = parse_payload(payload, item_map)
                all_bars.extend(bars); succeeded.update(symbols)
            except Exception as exc:
                errors.append(f"singleton {item['code']}: {type(exc).__name__}: {exc}")

    failed_symbols = [x["code"] for x in universe if x["code"] not in succeeded]
    backfill_bars: list[dict[str, Any]] = []
    backfill_errors: list[str] = []
    backfill_symbols: list[str] = []
    if args.backfill_days > 0 and args.source == "tencent":
        with connect(args.db) as db:
            short_history = symbols_needing_backfill(db, universe, args.minimum_history)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.backfill_workers)) as executor:
            futures = {executor.submit(fetch_baostock_history, item, args.backfill_days): item for item in short_history}
            for future in concurrent.futures.as_completed(futures):
                item = futures[future]
                try:
                    rows = future.result()
                    if not rows:
                        raise RuntimeError("no history rows")
                    backfill_bars.extend(rows); backfill_symbols.append(item["code"])
                except Exception as exc:
                    backfill_errors.append(f"{item['code']}: {type(exc).__name__}: {exc}")
        all_bars.extend(backfill_bars)

    elapsed = int((time.monotonic() - t0) * 1000)
    with connect(args.db) as db:
        upsert_instruments(db, full_universe)
        written = upsert_bars(db, all_bars)
        detail = {"bars_written": written, "errors": errors, "failed_symbols": failed_symbols,
                  "backfill_symbols": sorted(backfill_symbols), "backfill_errors": backfill_errors}
        if args.source == "tencent":
            detail["source_counts"] = dict(source_coverage)
        for src, cnt in source_coverage.items():
            audit(db, run_id=run_id, source=src, started_at=started,
                  requested=len(universe), succeeded=cnt, failed=0,
                  adjustment="qfq", latency_ms=elapsed,
                  status="ok", detail=detail)
        if args.source == "iwencai":
            audit(db, run_id=run_id, source="iwencai", started_at=started,
                  requested=len(universe), succeeded=len(succeeded), failed=len(universe) - len(succeeded),
                  adjustment="qfq", latency_ms=elapsed,
                  status="ok" if len(succeeded) == len(universe) else "partial",
                  detail=detail)
    backup_dir = args.db.parent / "backups"; backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"etf-compass-{datetime.now(CN).date().isoformat()}.db"
    with sqlite3.connect(args.db) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    backup_cutoff = time.time() - 30 * 86400
    for old in backup_dir.glob("etf-compass-*.db"):
        if old.stat().st_mtime < backup_cutoff:
            old.unlink()
    result = {"run_id": run_id, "requested": len(universe), "succeeded": len(succeeded),
              "failed": len(universe) - len(succeeded), "failed_symbols": failed_symbols,
              "bars_written": len(all_bars),
              "backfill_symbols": len(backfill_symbols), "backfill_errors": backfill_errors,
              "latency_ms": elapsed, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    required = math.ceil(len(universe) * 0.90)
    return 0 if len(succeeded) >= required else 2


if __name__ == "__main__":
    raise SystemExit(main())
