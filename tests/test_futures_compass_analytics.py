from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.futures_compass_analytics import (
    build_summary,
    enrich_item,
    fvg_state,
    structure_state,
    tick_round,
    trend_label,
)


def rows(count: int = 25):
    values = []
    for index in range(count):
        close = 100 + index
        values.append({
            "trade_date": f"2026-06-{index + 1:02d}",
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000 + index * 10,
            "open_interest": 5000 + index * 20,
        })
    return values


def test_structure_and_trend_are_deterministic():
    history = rows()
    structure = structure_state(history, 130)
    assert structure["structure"] == "向上BOS"
    assert trend_label(130, 122, 119, 114.5) == "多头排列"


def test_fvg_unknown_when_no_gap_exists():
    result = fvg_state(rows())
    assert result["direction"] == "无明确FVG"
    assert result["status"] == "未知"


def test_enrichment_preserves_unknown_receipt_and_builds_summary(tmp_path: Path):
    db = sqlite3.connect(tmp_path / "futures.db")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE daily_bars(code TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,open_interest REAL,settle REAL);
    CREATE TABLE warehouse_receipts(code TEXT,trade_date TEXT,receipt REAL,change_value REAL,source TEXT,fetched_at TEXT);
    """)
    for row in rows(25):
        db.execute(
            "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?)",
            ("LC", row["trade_date"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["open_interest"], row["close"]),
        )
    item = enrich_item(db, {
        "code": "LC", "continuous": "LC0", "name": "碳酸锂", "exchange": "广期所", "tick": 1,
        "price": 130, "change_pct": 2.2, "volume": 1800, "open_interest_change_pct": 3.1,
        "capital_state": "增仓上涨",
    })
    assert item["trend_state"] == "多头排列"
    assert item["structure"] == "向上BOS"
    assert item["signal_label"] == "多头确认"
    assert item["warehouse_receipt"]["status"] == "unknown"
    assert item["support"] < item["resistance"]
    assert tick_round(151991.43, 20) == 152000
    summary = build_summary([item, {**item, "code": "SI", "name": "工业硅", "change_pct": -1.5, "open_interest_change_pct": -2.0, "capital_state": "减仓下跌"}])
    assert summary["strongest"]["code"] == "LC"
    assert summary["weakest"]["code"] == "SI"
    assert summary["capital_counts"] == {"增仓上涨": 1, "减仓下跌": 1}
