"""韩精准制导 public signal dataset contract tests (offline, no network)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PATH = SCRIPTS / "generate_a_han_precise_signals.py"
spec = importlib.util.spec_from_file_location("generate_a_han_precise_signals", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _bars(count: int, start: float = 10.0, step: float = 0.05) -> list[tuple]:
    rows = []
    for i in range(count):
        close = start + i * step
        rows.append((f"2026-01-{i + 1:03d}", close, close, close * 1.01, close * 0.99, 1000.0))
    return rows


def test_short_history_produces_explicit_unavailable_row():
    row = module.build_row("123456", "测试ETF", _bars(50))
    assert row["status"] == "unavailable"
    assert row["reason"]
    assert row["background"] == "无背景"
    assert row["state"] == "无状态"
    assert row["signals"] == []
    assert row["key_levels"] is None
    assert row["metrics"] is None


def test_sufficient_history_produces_signal_row():
    row = module.build_row("123456", "测试ETF", _bars(200))
    assert row["status"] == "ok"
    assert row["reason"] is None
    assert row["bar_count"] == 200
    assert row["background"] in {"绿色", "橙色", "红色", "蓝色"}
    assert row["state"] in {"做多", "做空", "强洗", "弱升"}
    assert row["state_color"] in {"红色", "绿色", "橙色", "青色"}
    assert row["signal_date"] is not None
    assert row["key_levels"] is not None
    assert row["metrics"] is not None
    assert isinstance(row["signals"], list)


def test_published_row_contains_no_html_delimiters():
    row = module.build_row("123456", "测试ETF", _bars(200))
    serialized = json.dumps(row, ensure_ascii=False)
    assert "<" not in serialized
    assert ">" not in serialized


def test_published_row_contains_no_private_path():
    row = module.build_row("123456", "测试ETF", _bars(200))
    serialized = json.dumps(row, ensure_ascii=False)
    assert "/root/" not in serialized
    assert "/home/" not in serialized


def test_signal_names_are_published_verbatim():
    row = module.build_row("123456", "测试ETF", _bars(200))
    for signal in row["signals"]:
        assert signal["name"]
        assert signal["date"]
        assert set(signal) == {"name", "date", "close"}


def test_normalize_market_maps_pool_and_cache_labels():
    assert module.normalize_market("sh") == "XSHG"
    assert module.normalize_market("sz") == "XSHE"
    assert module.normalize_market("XSHG") == "XSHG"
    assert module.normalize_market("XSHE") == "XSHE"
    assert module.normalize_market("unknown") is None
    assert module.normalize_market(None) is None


def test_round_or_none_rejects_nan_and_bools():
    assert module.round_or_none(float("nan")) is None
    assert module.round_or_none(True) is None
    assert module.round_or_none(None) is None
    assert module.round_or_none(1.23456, 2) == 1.23


def test_build_payload_counts_valid_and_unavailable(tmp_path):
    db_path = tmp_path / "bars.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE daily_bars (market TEXT, symbol TEXT, trade_date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL, amount REAL, adjustment TEXT, "
        "source TEXT, is_final INTEGER, fetched_at TEXT, PRIMARY KEY "
        "(market, symbol, trade_date, adjustment, source))"
    )
    db.commit()
    db.close()

    reader = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    reader.row_factory = sqlite3.Row
    try:
        payload = module.build_payload(
            ["123456", "654321"], {"123456": "甲", "654321": "乙"},
            {"123456": "XSHE", "654321": "XSHE"}, reader,
        )
    finally:
        reader.close()
    # No bars in the empty cache → both rows are explicitly unavailable.
    assert payload["universe_count"] == 2
    assert payload["valid_count"] == 0
    assert sorted(payload["unavailable_codes"]) == ["123456", "654321"]
    assert payload["observation_date"] is None


def test_payload_fingerprint_ignores_generated_at():
    base = {
        "schema_version": "a-han-precise-signals-v1",
        "generated_at": "2026-10-26T22:00:00+08:00",
        "rows": [{"code": "123456"}],
    }
    rerun = {**base, "generated_at": "2026-10-27T22:00:00+08:00"}
    assert module.payload_fingerprint(base) == module.payload_fingerprint(rerun)
    changed = {**base, "rows": [{"code": "654321"}]}
    assert module.payload_fingerprint(base) != module.payload_fingerprint(changed)


def test_read_bars_uses_canonical_source_priority_dedupe(tmp_path, monkeypatch):
    """The cache can hold one row per (date, source); get_bars must pick one."""
    db_path = tmp_path / "bars.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE daily_bars (market TEXT, symbol TEXT, trade_date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL, amount REAL, adjustment TEXT, "
        "source TEXT, is_final INTEGER, fetched_at TEXT, PRIMARY KEY "
        "(market, symbol, trade_date, adjustment, source))"
    )
    db.execute(
        "INSERT INTO daily_bars VALUES ('XSHE','123456','2026-01-02',1,1,1,1,10,0,'qfq','tencent',1,'2026-01-02T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO daily_bars VALUES ('XSHE','123456','2026-01-02',2,2,2,2,20,0,'qfq','iwencai',1,'2026-01-03T00:00:00Z')"
    )
    db.commit()
    db.close()

    reader = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    reader.row_factory = sqlite3.Row
    try:
        rows = module.read_bars(reader, "123456", "XSHE")
    finally:
        reader.close()
    # tencent outranks iwencai in the project's source priority.
    assert len(rows) == 1
    assert rows[0][2] == 1.0
