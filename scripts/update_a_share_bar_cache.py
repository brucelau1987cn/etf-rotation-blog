#!/usr/bin/env python3
"""Incrementally import A-share ETF qfq bars from iWenCai into SQLite."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
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
    bars = [x for x in bars_by_key.values() if x.get("close") is not None]
    symbols = {x["symbol"] for x in bars}
    return bars, symbols


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--symbols", help="comma-separated subset for repair runs")
    args = parser.parse_args()
    full_universe = load_universe()
    wanted = {x.strip() for x in (args.symbols or "").split(",") if x.strip()}
    universe = [x for x in full_universe if not wanted or x["code"] in wanted]
    item_map = {x["code"]: x for x in universe}
    run_id = "iwencai-" + datetime.now(CN).strftime("%Y%m%d-%H%M%S")
    started = utc_now(); t0 = time.monotonic(); all_bars: list[dict[str, Any]] = []
    succeeded: set[str] = set(); errors: list[str] = []
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
    # Natural-language batching can occasionally return metadata without its time
    # series. Retry only those symbols in smaller groups.
    missing = [x for x in universe if x["code"] not in succeeded]
    for start in range(0, len(missing), 4):
        batch = missing[start:start + 4]
        try:
            payload = query_batch(batch, args.days)
            bars, symbols = parse_payload(payload, item_map)
            all_bars.extend(bars); succeeded.update(symbols)
            raw_path = RAW_ROOT / f"{run_id}-repair-{start // 4 + 1:02d}.json"
            raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            errors.append(f"repair {start // 4 + 1}: {type(exc).__name__}: {exc}")
    # Some newer ETFs are recognized individually but not when four codes share one
    # natural-language query. Give the final missing set a singleton retry.
    remaining = [x for x in universe if x["code"] not in succeeded]
    for index, item in enumerate(remaining, 1):
        try:
            payload = query_batch([item], args.days)
            bars, symbols = parse_payload(payload, item_map)
            all_bars.extend(bars); succeeded.update(symbols)
            raw_path = RAW_ROOT / f"{run_id}-singleton-{index:02d}.json"
            raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            errors.append(f"singleton {item['code']}: {type(exc).__name__}: {exc}")
    failed_symbols = [x["code"] for x in universe if x["code"] not in succeeded]
    elapsed = int((time.monotonic() - t0) * 1000)
    with connect(args.db) as db:
        upsert_instruments(db, full_universe)
        written = upsert_bars(db, all_bars)
        audit(db, run_id=run_id, source="iwencai", started_at=started,
              requested=len(universe), succeeded=len(succeeded), failed=len(universe) - len(succeeded),
              adjustment="qfq", latency_ms=elapsed,
              status="ok" if len(succeeded) == len(universe) else "partial",
              detail={"bars_written": written, "errors": errors, "failed_symbols": failed_symbols})
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
              "latency_ms": elapsed, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    required = math.ceil(len(universe) * 0.90)
    return 0 if len(succeeded) >= required else 2


if __name__ == "__main__":
    raise SystemExit(main())
