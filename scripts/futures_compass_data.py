#!/usr/bin/env python3
"""Data layer for the futures compass: AkShare realtime, iWenCai reviews, SQLite audit."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from futures_compass_analytics import build_summary, enrich_item

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "local" / "futures" / "futures.db"
LIVE_SNAPSHOT = ROOT / "data" / "local" / "futures" / "live.json"
PUBLIC_SNAPSHOT = ROOT / "public" / "data" / "futures-compass.json"
IWENCAI_WRAPPER = Path.home() / ".hermes" / "scripts" / "iwencai-skill-run"
CN = ZoneInfo("Asia/Shanghai")

WATCHLIST = [
    {"code": "LC", "continuous": "LC0", "name": "碳酸锂", "exchange": "广期所", "unit": "元/吨", "tick": 20},
    {"code": "PS", "continuous": "PS0", "name": "多晶硅", "exchange": "广期所", "unit": "元/吨", "tick": 5},
    {"code": "SI", "continuous": "SI0", "name": "工业硅", "exchange": "广期所", "unit": "元/吨", "tick": 5},
    {"code": "AU", "continuous": "AU0", "name": "黄金", "exchange": "上期所", "unit": "元/克", "tick": 0.02},
    {"code": "AG", "continuous": "AG0", "name": "白银", "exchange": "上期所", "unit": "元/千克", "tick": 1},
    {"code": "CU", "continuous": "CU0", "name": "沪铜", "exchange": "上期所", "unit": "元/吨", "tick": 10},
    {"code": "AL", "continuous": "AL0", "name": "沪铝", "exchange": "上期所", "unit": "元/吨", "tick": 5},
    {"code": "SC", "continuous": "SC0", "name": "原油", "exchange": "能源中心", "unit": "元/桶", "tick": 0.1},
    {"code": "LH", "continuous": "LH0", "name": "生猪", "exchange": "大商所", "unit": "元/吨", "tick": 1},
]


def validate_public_snapshot(
    payload: dict[str, Any], *, now: datetime | None = None, max_age_seconds: int = 72 * 3600,
) -> list[str]:
    errors: list[str] = []
    current = (now or datetime.now(CN)).astimezone(CN)
    try:
        generated = datetime.fromisoformat(str(payload.get("generated_at") or "").replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=CN)
        age = (current - generated.astimezone(CN)).total_seconds()
        if age < -300:
            errors.append("futures snapshot generated_at is in the future")
        elif age > max_age_seconds:
            errors.append(f"futures snapshot is older than {max_age_seconds} seconds")
    except ValueError:
        errors.append("futures snapshot generated_at is invalid")

    expected = [item["code"] for item in WATCHLIST]
    if payload.get("ok") is not True:
        errors.append("futures snapshot ok must be true")
    if payload.get("stale") is not False:
        errors.append("futures snapshot stale must be false")
    if payload.get("expected_count") != len(expected):
        errors.append(f"futures snapshot expected_count must equal {len(expected)}")
    if not str(payload.get("source") or "").strip():
        errors.append("futures snapshot source is required")
    if payload.get("errors") != []:
        errors.append("futures snapshot errors must be empty")

    items = payload.get("items")
    actual = [str(item.get("code") or "") for item in items] if isinstance(items, list) else []
    if actual != expected or payload.get("count") != len(expected):
        errors.append(f"futures snapshot watchlist mismatch: expected={expected}, actual={actual}")
    summary = payload.get("summary")
    ranking = summary.get("ranking") if isinstance(summary, dict) else None
    if not isinstance(ranking, list) or len(ranking) != len(expected) or set(ranking) != set(expected):
        errors.append("futures snapshot summary ranking must cover all nine instruments")

    required_strings = (
        "continuous", "name", "exchange", "contract_code", "contract_name", "quote_time",
        "trade_date", "source", "capital_state", "trend_state", "structure", "signal_label",
    )
    required_numbers = (
        "price", "open", "high", "low", "prev_close", "volume", "open_interest",
        "ma5", "ma10", "ma20", "atr14", "support", "resistance", "invalidation",
    )
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            errors.append("futures snapshot item must be an object")
            continue
        code = str(item.get("code") or "<empty>")
        missing = [field for field in required_strings if not str(item.get(field) or "").strip()]
        invalid_numbers = []
        for field in required_numbers:
            value = number(item.get(field))
            if value is None or not math.isfinite(value):
                invalid_numbers.append(field)
        if missing or invalid_numbers:
            errors.append(f"futures snapshot {code} missing core fields: strings={missing}, numbers={invalid_numbers}")
            continue
        positive = ("price", "open", "high", "low", "prev_close", "ma5", "ma10", "ma20", "atr14", "support", "resistance", "invalidation")
        if any((number(item.get(field)) or 0.0) <= 0 for field in positive):
            errors.append(f"futures snapshot {code} core prices and indicators must be positive")
        if (number(item.get("high")) or 0.0) < (number(item.get("low")) or 0.0):
            errors.append(f"futures snapshot {code} high must be at least low")
        fvg = item.get("fvg")
        if not isinstance(fvg, dict) or not fvg.get("direction") or not fvg.get("status") or number(fvg.get("lower")) is None or number(fvg.get("upper")) is None:
            errors.append(f"futures snapshot {code} FVG structure is incomplete")
        warehouse = item.get("warehouse_receipt")
        if not isinstance(warehouse, dict) or warehouse.get("status") not in {"known", "unknown"}:
            errors.append(f"futures snapshot {code} warehouse receipt structure is incomplete")
        elif warehouse.get("status") == "known" and (
            not warehouse.get("trade_date") or number(warehouse.get("receipt")) is None or not warehouse.get("source")
        ):
            errors.append(f"futures snapshot {code} known warehouse receipt lacks data")
    return errors


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def number(value: Any) -> float | None:
    if value is None or str(value).strip() in {"", "--", "nan", "None"}:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS instruments(
      code TEXT PRIMARY KEY, continuous TEXT NOT NULL, name TEXT NOT NULL, exchange TEXT NOT NULL,
      main_contract TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS quotes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, observed_at TEXT NOT NULL,
      contract_code TEXT, contract_name TEXT, price REAL, change_pct REAL, open REAL, high REAL, low REAL,
      prev_close REAL, volume REAL, open_interest REAL, source TEXT NOT NULL,
      UNIQUE(code, observed_at, source)
    );
    CREATE INDEX IF NOT EXISTS idx_quotes_code_time ON quotes(code, observed_at DESC);
    CREATE TABLE IF NOT EXISTS daily_bars(
      code TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
      volume REAL, open_interest REAL, settle REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY(code, trade_date, source)
    );
    CREATE TABLE IF NOT EXISTS iwencai_reviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT, reviewed_at TEXT NOT NULL, review_slot TEXT NOT NULL,
      query TEXT NOT NULL, code_count INTEGER, row_count INTEGER, payload_json TEXT NOT NULL,
      status TEXT NOT NULL, error TEXT
    );
    CREATE TABLE IF NOT EXISTS warehouse_receipts(
      code TEXT NOT NULL, trade_date TEXT NOT NULL, receipt REAL, change_value REAL,
      source TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY(code, trade_date, source)
    );
    CREATE TABLE IF NOT EXISTS source_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, dataset TEXT NOT NULL,
      fetched_at TEXT NOT NULL, status TEXT NOT NULL, row_count INTEGER, latency_ms INTEGER, error TEXT
    );
    """)
    db.commit()
    return db


def audit(db: sqlite3.Connection, source: str, dataset: str, status: str, row_count: int = 0,
          latency_ms: int = 0, error: str | None = None) -> None:
    db.execute(
        "INSERT INTO source_audit(source,dataset,fetched_at,status,row_count,latency_ms,error) VALUES(?,?,?,?,?,?,?)",
        (source, dataset, now_iso(), status, row_count, latency_ms, error),
    )


def load_snapshot() -> dict[str, Any] | None:
    for path in (LIVE_SNAPSHOT, PUBLIC_SNAPSHOT):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("items"):
                return payload
        except (OSError, ValueError, TypeError):
            continue
    return None


def latest_review(db: sqlite3.Connection) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT reviewed_at,review_slot,code_count,row_count,status,error FROM iwencai_reviews ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def fetch_realtime() -> dict[str, Any]:
    import akshare as ak  # pyright: ignore[reportMissingImports]

    started = time.time()
    observed_at = now_iso()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    with connect() as db:
        for meta in WATCHLIST:
            try:
                frame = ak.futures_zh_realtime(symbol=meta["name"])
                records = frame.to_dict("records")
                continuous = next((r for r in records if str(r.get("symbol", "")).upper() == meta["continuous"]), None)
                monthly = [r for r in records if str(r.get("symbol", "")).upper().startswith(meta["code"]) and str(r.get("symbol", "")).upper() != meta["continuous"]]
                main = max(monthly, key=lambda r: number(r.get("volume")) or -1, default=continuous or {})
                row = continuous or main
                if not row:
                    raise RuntimeError("empty quote")
                change = number(row.get("changepercent"))
                if change is not None and abs(change) <= 1:
                    change *= 100
                price = number(row.get("trade"))
                high = number(row.get("high"))
                low = number(row.get("low"))
                prev_close = number(row.get("preclose"))
                open_interest = number(row.get("position"))
                trade_date = str(row.get("tradedate") or datetime.now(CN).date().isoformat()).replace("-", "")
                prior = db.execute(
                    "SELECT open_interest FROM daily_bars WHERE code=? AND replace(trade_date,'-','')<? "
                    "ORDER BY trade_date DESC LIMIT 1",
                    (meta["code"], trade_date),
                ).fetchone()
                prior_oi = number(prior["open_interest"]) if prior else None
                oi_change = open_interest - prior_oi if open_interest is not None and prior_oi is not None else None
                oi_change_pct = oi_change / prior_oi * 100 if oi_change is not None and prior_oi else None
                amplitude_pct = (high - low) / prev_close * 100 if high is not None and low is not None and prev_close else None
                if change is None or oi_change is None:
                    capital_state = "等待量仓确认"
                elif change > 0 and oi_change > 0:
                    capital_state = "增仓上涨"
                elif change < 0 and oi_change > 0:
                    capital_state = "增仓下跌"
                elif change > 0 and oi_change < 0:
                    capital_state = "减仓上涨"
                elif change < 0 and oi_change < 0:
                    capital_state = "减仓下跌"
                else:
                    capital_state = "量仓平衡"
                item = {
                    **meta,
                    "contract_code": main.get("symbol") or row.get("symbol"),
                    "contract_name": main.get("name") or row.get("name") or meta["name"],
                    "price": price,
                    "change_pct": round(change, 3) if change is not None else None,
                    "open": number(row.get("open")), "high": high,
                    "low": low, "prev_close": prev_close,
                    "amplitude_pct": round(amplitude_pct, 3) if amplitude_pct is not None else None,
                    "volume": number(row.get("volume")), "open_interest": open_interest,
                    "open_interest_change": oi_change,
                    "open_interest_change_pct": round(oi_change_pct, 3) if oi_change_pct is not None else None,
                    "capital_state": capital_state,
                    "quote_time": row.get("ticktime"), "trade_date": row.get("tradedate"),
                    "is_main": True, "source": "新浪期货",
                }
                items.append(item)
                db.execute(
                    "INSERT INTO instruments(code,continuous,name,exchange,main_contract,updated_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(code) DO UPDATE SET main_contract=excluded.main_contract,updated_at=excluded.updated_at",
                    (meta["code"], meta["continuous"], meta["name"], meta["exchange"], item["contract_code"], observed_at),
                )
                db.execute(
                    "INSERT OR IGNORE INTO quotes(code,observed_at,contract_code,contract_name,price,change_pct,open,high,low,prev_close,volume,open_interest,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (meta["code"], observed_at, item["contract_code"], item["contract_name"], item["price"], item["change_pct"],
                     item["open"], item["high"], item["low"], item["prev_close"], item["volume"], item["open_interest"], "sina-akshare"),
                )
            except Exception as exc:
                errors.append(f"{meta['code']}: {exc}")
        latency = round((time.time() - started) * 1000)
        status = "ok" if len(items) == len(WATCHLIST) else "partial" if items else "error"
        audit(db, "sina-akshare", "realtime", status, len(items), latency, "; ".join(errors)[:500] or None)
        db.commit()
        items = [enrich_item(db, item) for item in items]
        review = latest_review(db)
    if len(items) < 6:
        raise RuntimeError(f"realtime coverage too low: {len(items)}/9; {'; '.join(errors)}")
    payload = {
        "ok": True, "source": "新浪期货", "generated_at": observed_at,
        "fetched_at": time.time(), "latency_ms": latency, "count": len(items),
        "expected_count": len(WATCHLIST), "stale": False, "errors": errors,
        "iwencai_review": review, "summary": build_summary(items), "items": items,
    }
    atomic_json(LIVE_SNAPSHOT, payload)
    return payload


def fetch_daily_bars() -> dict[str, Any]:
    import akshare as ak  # pyright: ignore[reportMissingImports]

    started = time.time(); rows = 0; errors = []
    with connect() as db:
        for meta in WATCHLIST:
            try:
                frame = ak.futures_zh_daily_sina(symbol=meta["continuous"])
                for bar in frame.tail(60).to_dict("records"):
                    db.execute(
                        "INSERT INTO daily_bars(code,trade_date,open,high,low,close,volume,open_interest,settle,source,fetched_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code,trade_date,source) DO UPDATE SET "
                        "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,"
                        "open_interest=excluded.open_interest,settle=excluded.settle,fetched_at=excluded.fetched_at",
                        (meta["code"], str(bar.get("date")), number(bar.get("open")), number(bar.get("high")),
                         number(bar.get("low")), number(bar.get("close")), number(bar.get("volume")),
                         number(bar.get("hold")), number(bar.get("settle")), "sina-akshare", now_iso()),
                    )
                    rows += 1
            except Exception as exc:
                errors.append(f"{meta['code']}: {exc}")
        latency = round((time.time() - started) * 1000)
        audit(db, "sina-akshare", "daily_bars", "ok" if not errors else "partial", rows, latency, "; ".join(errors)[:500] or None)
        db.commit()
    return {"status": "ok" if rows else "error", "rows": rows, "errors": errors}


def run_iwencai_review(slot: str) -> dict[str, Any]:
    query = "碳酸锂 多晶硅 工业硅 黄金 白银 沪铜 沪铝 原油 生猪主力合约最新价涨跌幅成交量持仓量"
    command = [str(IWENCAI_WRAPPER), "hithink-futures-query", "--query", query, "--limit", "20", "--timeout", "45"]
    started = time.time(); reviewed_at = now_iso()
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=60)
    status = "ok" if proc.returncode == 0 else "error"
    error = None
    payload: dict[str, Any] = {}
    if status == "ok":
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            status = "error"; error = str(exc)
    else:
        error = (proc.stderr or proc.stdout or "iWenCai failed").strip()[:500]
    rows = payload.get("datas") or []
    with connect() as db:
        db.execute(
            "INSERT INTO iwencai_reviews(reviewed_at,review_slot,query,code_count,row_count,payload_json,status,error) VALUES(?,?,?,?,?,?,?,?)",
            (reviewed_at, slot, query, payload.get("code_count"), len(rows), json.dumps(payload, ensure_ascii=False), status, error),
        )
        audit(db, "iwencai", "scheduled_review", status, len(rows), round((time.time() - started) * 1000), error)
        db.commit()
    return {"status": status, "reviewed_at": reviewed_at, "slot": slot, "code_count": payload.get("code_count"), "rows": len(rows), "error": error}


def fetch_warehouse_receipts() -> dict[str, Any]:
    import akshare as ak  # pyright: ignore[reportMissingImports]

    started = time.time(); rows = 0; errors = []
    try:
        result = ak.futures_gfex_warehouse_receipt()
    except Exception as exc:
        result = {}; errors.append(str(exc))
    today = datetime.now(CN).date().isoformat()
    with connect() as db:
        for code in ("LC", "PS", "SI"):
            try:
                frame = result.get(code)
                if frame is None or frame.empty:
                    continue
                receipt = sum(number(v) or 0 for v in frame.get("今日仓单量", []))
                change = sum(number(v) or 0 for v in frame.get("增减", []))
                db.execute(
                    "INSERT INTO warehouse_receipts(code,trade_date,receipt,change_value,source,fetched_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(code,trade_date,source) DO UPDATE SET receipt=excluded.receipt,change_value=excluded.change_value,fetched_at=excluded.fetched_at",
                    (code, today, receipt, change, "gfex-akshare", now_iso()),
                )
                rows += 1
            except Exception as exc:
                errors.append(f"{code}: {exc}")
        audit(db, "gfex-akshare", "warehouse_receipts", "ok" if rows else "error", rows,
              round((time.time() - started) * 1000), "; ".join(errors)[:500] or None)
        db.commit()
    return {"status": "ok" if rows else "error", "rows": rows, "errors": errors}


def snapshot_with_fallback() -> dict[str, Any]:
    try:
        return fetch_realtime()
    except Exception as exc:
        payload = load_snapshot()
        if not payload:
            raise
        payload = dict(payload)
        payload.update({"ok": True, "stale": True, "warning": str(exc)[:300]})
        with connect() as db:
            payload["iwencai_review"] = latest_review(db)
        return payload
