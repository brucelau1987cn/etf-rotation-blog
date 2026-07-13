#!/usr/bin/env python3
"""Local point-in-time daily-bar store for ETF Compass."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "local" / "etf-compass.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS instruments (
  market TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
  asset_type TEXT, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
  PRIMARY KEY (market, symbol)
);
CREATE TABLE IF NOT EXISTS daily_bars (
  market TEXT NOT NULL, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL, amount REAL,
  adjustment TEXT NOT NULL, source TEXT NOT NULL, is_final INTEGER NOT NULL DEFAULT 1,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (market, symbol, trade_date, adjustment, source)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_lookup
  ON daily_bars(market, symbol, adjustment, trade_date DESC);
CREATE TABLE IF NOT EXISTS source_audit (
  run_id TEXT NOT NULL, source TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, requested INTEGER NOT NULL DEFAULT 0,
  succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
  adjustment TEXT, latency_ms INTEGER, status TEXT NOT NULL, detail TEXT,
  PRIMARY KEY (run_id, source)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path = DEFAULT_DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def upsert_instruments(db: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> None:
    now = utc_now()
    db.executemany(
        """INSERT INTO instruments(market,symbol,name,asset_type,updated_at)
           VALUES(?,?,?,?,?) ON CONFLICT(market,symbol) DO UPDATE SET
           name=excluded.name,asset_type=excluded.asset_type,active=1,updated_at=excluded.updated_at""",
        [(x["market"], x["code"], x["name"], x.get("type"), now) for x in items],
    )


def upsert_bars(db: sqlite3.Connection, bars: Iterable[dict[str, Any]]) -> int:
    rows = list(bars)
    now = utc_now()
    db.executemany(
        """INSERT INTO daily_bars
           (market,symbol,trade_date,open,high,low,close,volume,amount,adjustment,source,is_final,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(market,symbol,trade_date,adjustment,source) DO UPDATE SET
           open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
           volume=excluded.volume,amount=excluded.amount,is_final=excluded.is_final,
           fetched_at=excluded.fetched_at""",
        [(
            x["market"], x["symbol"], x["trade_date"], x.get("open"), x.get("high"),
            x.get("low"), x["close"], x.get("volume"), x.get("amount"),
            x["adjustment"], x["source"], int(x.get("is_final", True)), x.get("fetched_at", now),
        ) for x in rows],
    )
    return len(rows)


def get_bars(db: sqlite3.Connection, market: str, symbol: str, adjustment: str = "qfq", limit: int = 90) -> list[dict[str, Any]]:
    # Point-in-time source priority. One row per date is selected deterministically.
    priority = "CASE source WHEN 'iwencai' THEN 1 WHEN 'stock-api' THEN 2 WHEN 'tencent' THEN 3 ELSE 9 END"
    rows = db.execute(
        f"""SELECT * FROM (
          SELECT *, ROW_NUMBER() OVER(PARTITION BY trade_date ORDER BY {priority}, fetched_at DESC) AS rn
          FROM daily_bars WHERE market=? AND symbol=? AND adjustment=? AND is_final=1
        ) WHERE rn=1 ORDER BY trade_date DESC LIMIT ?""",
        (market, symbol, adjustment, limit),
    ).fetchall()
    return [dict(x) for x in reversed(rows)]


def audit(db: sqlite3.Connection, *, run_id: str, source: str, started_at: str,
          requested: int, succeeded: int, failed: int, adjustment: str,
          latency_ms: int, status: str, detail: dict[str, Any] | str | None = None) -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False)
    db.execute(
        """INSERT OR REPLACE INTO source_audit
           (run_id,source,started_at,finished_at,requested,succeeded,failed,adjustment,latency_ms,status,detail)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, source, started_at, utc_now(), requested, succeeded, failed,
         adjustment, latency_ms, status, text),
    )
