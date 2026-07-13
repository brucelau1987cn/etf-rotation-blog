#!/usr/bin/env python3
"""Local daily-bar store for the US ETF Compass.

Yahoo remains the authoritative price source. This cache exists for:
- reproducible close generation
- offline / rate-limit fallback
- audit of trigger-base history
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "local" / "us-etf-compass.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  asset_type TEXT,
  theme TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_bars (
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL NOT NULL,
  adj_close REAL,
  volume REAL,
  source TEXT NOT NULL DEFAULT 'yahoo',
  is_final INTEGER NOT NULL DEFAULT 1,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, trade_date, source)
);
CREATE INDEX IF NOT EXISTS idx_us_daily_bars_lookup
  ON daily_bars(symbol, is_final, trade_date DESC);
CREATE TABLE IF NOT EXISTS source_audit (
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  requested INTEGER NOT NULL DEFAULT 0,
  succeeded INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  status TEXT NOT NULL,
  detail TEXT,
  PRIMARY KEY (run_id, source)
);
CREATE TABLE IF NOT EXISTS selector_hits (
  run_id TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  query TEXT NOT NULL,
  symbol TEXT,
  name TEXT,
  rank_no INTEGER,
  payload TEXT,
  PRIMARY KEY (run_id, query, rank_no)
);
CREATE TABLE IF NOT EXISTS selector_runs (
  run_id TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  formal_overlap INTEGER NOT NULL DEFAULT 0,
  new_candidates INTEGER NOT NULL DEFAULT 0,
  detail TEXT
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
        """INSERT INTO instruments(symbol,name,asset_type,theme,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(symbol) DO UPDATE SET
             name=excluded.name,
             asset_type=excluded.asset_type,
             theme=excluded.theme,
             active=1,
             updated_at=excluded.updated_at""",
        [
            (
                x["symbol"],
                x["name"],
                x.get("asset_type"),
                x.get("theme"),
                now,
            )
            for x in items
        ],
    )


def upsert_bars(db: sqlite3.Connection, bars: Iterable[dict[str, Any]]) -> int:
    rows = list(bars)
    now = utc_now()
    db.executemany(
        """INSERT INTO daily_bars
           (symbol,trade_date,open,high,low,close,adj_close,volume,source,is_final,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol,trade_date,source) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
             adj_close=excluded.adj_close, volume=excluded.volume,
             is_final=excluded.is_final, fetched_at=excluded.fetched_at""",
        [
            (
                x["symbol"],
                x["trade_date"],
                x.get("open"),
                x.get("high"),
                x.get("low"),
                x["close"],
                x.get("adj_close"),
                x.get("volume"),
                x.get("source", "yahoo"),
                int(x.get("is_final", True)),
                x.get("fetched_at", now),
            )
            for x in rows
        ],
    )
    return len(rows)


def get_bars(
    db: sqlite3.Connection,
    symbol: str,
    *,
    source: str = "yahoo",
    limit: int = 520,
    final_only: bool = False,
) -> list[dict[str, Any]]:
    clause = "AND is_final=1" if final_only else ""
    rows = db.execute(
        f"""SELECT symbol, trade_date, open, high, low, close, adj_close, volume, source, is_final, fetched_at
            FROM daily_bars
            WHERE symbol=? AND source=? {clause}
            ORDER BY trade_date DESC
            LIMIT ?""",
        (symbol, source, limit),
    ).fetchall()
    return [dict(x) for x in reversed(rows)]


def coverage(db: sqlite3.Connection, symbols: list[str], trade_date: str, source: str = "yahoo") -> int:
    if not symbols:
        return 0
    placeholders = ",".join("?" for _ in symbols)
    row = db.execute(
        f"""SELECT COUNT(DISTINCT symbol) FROM daily_bars
            WHERE source=? AND trade_date=? AND symbol IN ({placeholders})""",
        [source, trade_date, *symbols],
    ).fetchone()
    return int(row[0] if row else 0)


def latest_trade_date(db: sqlite3.Connection, source: str = "yahoo") -> str | None:
    row = db.execute(
        "SELECT max(trade_date) FROM daily_bars WHERE source=? AND is_final=1",
        (source,),
    ).fetchone()
    return row[0] if row and row[0] else None


def audit(
    db: sqlite3.Connection,
    *,
    run_id: str,
    source: str,
    started_at: str,
    requested: int,
    succeeded: int,
    failed: int,
    latency_ms: int,
    status: str,
    detail: dict[str, Any] | str | None = None,
) -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False)
    db.execute(
        """INSERT OR REPLACE INTO source_audit
           (run_id,source,started_at,finished_at,requested,succeeded,failed,latency_ms,status,detail)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id, source, started_at, utc_now(), requested, succeeded, failed, latency_ms, status, text),
    )


def save_selector_run(
    db: sqlite3.Connection,
    *,
    run_id: str,
    generated_at: str,
    status: str,
    candidate_count: int,
    formal_overlap: int,
    new_candidates: int,
    detail: dict[str, Any],
    hits: list[dict[str, Any]],
) -> None:
    db.execute(
        """INSERT OR REPLACE INTO selector_runs
           (run_id,generated_at,status,candidate_count,formal_overlap,new_candidates,detail)
           VALUES(?,?,?,?,?,?,?)""",
        (
            run_id,
            generated_at,
            status,
            candidate_count,
            formal_overlap,
            new_candidates,
            json.dumps(detail, ensure_ascii=False),
        ),
    )
    db.execute("DELETE FROM selector_hits WHERE run_id=?", (run_id,))
    db.executemany(
        """INSERT INTO selector_hits(run_id,generated_at,query,symbol,name,rank_no,payload)
           VALUES(?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                generated_at,
                h.get("query") or "",
                h.get("symbol"),
                h.get("name"),
                int(h.get("rank_no") or 0),
                json.dumps(h.get("payload") or {}, ensure_ascii=False),
            )
            for h in hits
        ],
    )


def bars_to_generator_rows(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert DB bars into generate_us_etf_garden.fetch row shape."""
    rows = []
    for bar in bars:
        close = bar.get("close")
        adj = bar.get("adj_close")
        if close is None:
            continue
        rows.append(
            {
                "date": bar["trade_date"],
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": close,
                "volume": bar.get("volume") or 0,
                "adj": adj if adj is not None else close,
            }
        )
    return rows
