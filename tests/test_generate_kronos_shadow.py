import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from scripts import generate_kronos_shadow as kronos


class FakeRuntime:
    torch_version = "fake"

    def __init__(self):
        self.calls = 0

    def predict(self, frames, future_sessions):
        self.calls += 1
        result = {}
        for symbol, frame in frames.items():
            close = float(frame["close"].iloc[-1])
            rows = []
            for step in range(1, 6):
                predicted = close * (1 + 0.002 * step)
                rows.append({
                    "open": predicted * 0.999,
                    "high": predicted * 1.004,
                    "low": predicted * 0.996,
                    "close": predicted,
                    "volume": 0.0,
                    "amount": 0.0,
                })
            result[symbol] = pd.DataFrame(rows, index=future_sessions)
        return result


class BombRuntime:
    torch_version = "bomb"

    def predict(self, frames, future_sessions):
        raise AssertionError("cache miss")


def seed_db(path: Path, symbols=("510001", "510002"), bars=100):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE daily_bars (
              market TEXT, symbol TEXT, trade_date TEXT, open REAL, high REAL,
              low REAL, close REAL, volume REAL, amount REAL, adjustment TEXT,
              source TEXT, is_final INTEGER, fetched_at TEXT
            );
            """
        )
        dates = pd.bdate_range("2026-02-27", periods=bars)
        for offset, symbol in enumerate(symbols):
            for index, trade_date in enumerate(dates):
                close = 1.0 + offset + index * 0.001
                db.execute(
                    "INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "A", symbol, trade_date.date().isoformat(), close * 0.999,
                        close * 1.005, close * 0.995, close, 1000, 1000 * close,
                        "qfq", "iwencai", 1, "2026-07-16T16:00:00+08:00",
                    ),
                )


def test_generate_shadow_payload_and_reuse_cache(tmp_path):
    db = tmp_path / "bars.db"
    out = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    seed_db(db)
    universe = [
        {"symbol": "510001", "name": "测试一"},
        {"symbol": "510002", "name": "测试二"},
    ]
    sessions = list(pd.bdate_range("2026-07-20", periods=5))
    runtime = FakeRuntime()
    payload = kronos.generate(
        db_path=db, out_path=out, history_path=history, runtime=runtime,
        expected_symbols=2, universe=universe, future_sessions=sessions,
        minimum_history=96, lookback=100,
    )
    assert runtime.calls == 1
    assert payload["mode"] == "shadow_research_only"
    assert payload["production_weights_changed"] is False
    assert payload["formal_signal_logic_changed"] is False
    assert payload["production_role"] == "display_and_audit_only"
    assert payload["coverage"]["predicted_symbols"] == 2
    assert len(payload["items"]) == 2
    assert all(len(item["steps"]) == 5 for item in payload["items"])
    assert all(item["five_day"]["predicted_return_pct"] == 1.0 for item in payload["items"])
    assert not kronos.validate_payload(payload, 2)
    cached = kronos.generate(
        db_path=db, out_path=out, history_path=history, runtime=BombRuntime(),
        expected_symbols=2, universe=universe, future_sessions=sessions,
        minimum_history=96, lookback=100,
    )
    assert cached["runtime"]["cache_hit"] is True
    assert len(history.read_text().splitlines()) == 1


def test_history_failure_preserves_previous_public_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "bars.db"
    out = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    seed_db(db)
    out.write_text('{"previous":true}\n')
    universe = [
        {"symbol": "510001", "name": "测试一"},
        {"symbol": "510002", "name": "测试二"},
    ]
    monkeypatch.setattr(kronos, "append_history", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        kronos.generate(
            db_path=db, out_path=out, history_path=history, runtime=FakeRuntime(),
            expected_symbols=2, universe=universe,
            future_sessions=list(pd.bdate_range("2026-07-20", periods=5)),
            minimum_history=96, lookback=100, reuse_cache=False,
        )
    assert json.loads(out.read_text()) == {"previous": True}


def test_raw_ohlc_validation_reports_model_violation():
    prediction = pd.DataFrame([
        {"open": 10.0, "high": 9.0, "low": 11.0, "close": 10.0}
        for _ in range(5)
    ])
    valid, errors = kronos.validate_raw_prediction(prediction)
    assert valid is False
    assert "OHLC envelope violation" in errors
