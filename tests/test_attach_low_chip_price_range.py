"""一年内/三年内最低价（不复权）契约测试。

覆盖：窗口边界、no-lookahead、最低价取 low 而非 close、覆盖不完整标记、
数据源降级、fail-closed、页面/D1/sync 字段成套接线。
全程不联网：fetch_* 全部 monkeypatch。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_price_range as mod  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"


def bars(*rows):
    """rows: (date, low, close)"""
    return [{"trade_date": d, "low": low, "close": close} for d, low, close in rows]


def test_min_uses_daily_low_not_close():
    series = bars(
        ("2024-01-02", 5.0, 9.0),
        ("2025-06-10", 4.2, 8.8),
        ("2026-10-30", 6.6, 6.9),
    )
    m = mod.price_range_metrics(series, "2026-11-06", 10.0)
    assert m["status"] == "ok"
    # low 取每日最低价，不取收盘：2024-01-02 收盘 9.0 但最低 5.0
    assert m["low_3y"] == 4.2 and m["low_3y_date"] == "2025-06-10"
    # 1 年窗口（>= 2025-11-06）只剩 2026-10-30 一根
    assert m["low_1y"] == 6.6 and m["low_1y_date"] == "2026-10-30"


def test_one_year_window_is_365_days_and_three_year_is_calendar_year():
    series = bars(
        ("2023-11-10", 3.0, 3.1),   # 3 年内、1 年外
        ("2025-11-10", 4.0, 4.1),   # 1 年内
        ("2026-11-05", 7.0, 7.1),
    )
    m = mod.price_range_metrics(series, "2026-11-06", 8.0)
    assert m["window_1y_start"] == "2025-11-06"
    assert m["window_3y_start"] == "2023-11-06"
    assert m["low_1y"] == 4.0 and m["low_1y_date"] == "2025-11-10"
    assert m["low_3y"] == 3.0 and m["low_3y_date"] == "2023-11-10"

    # 早于 as_of-3 年的行不参与三年窗口
    older = bars(("2022-11-01", 1.0, 1.1), *[("2023-11-10", 3.0, 3.1), ("2026-11-05", 7.0, 7.1)])
    assert mod.price_range_metrics(older, "2026-11-06", 8.0)["low_3y"] == 3.0


def test_distance_percent_uses_snapshot_price():
    series = bars(("2026-01-05", 10.0, 10.5), ("2026-11-05", 12.0, 12.5))
    m = mod.price_range_metrics(series, "2026-11-06", 15.0)
    # 1 年 / 3 年窗口内最低价都是 10.0（2026-01-05）
    assert m["from_low_3y_pct"] == 50.0
    assert m["from_low_1y_pct"] == 50.0
    assert m["price"] == 15.0


def test_no_lookahead_rows_after_as_of_are_dropped():
    raw = bars(("2026-11-05", 9.0, 9.1), ("2026-11-09", 1.0, 1.0))
    normalized = mod._normalize(raw, "2026-11-06")
    assert [row["trade_date"] for row in normalized] == ["2026-11-05"]
    m = mod.price_range_metrics(normalized, "2026-11-06", 10.0)
    assert m["low_3y"] == 9.0


def test_new_listing_marks_incomplete_coverage_but_still_reports():
    series = bars(("2025-12-01", 20.0, 20.5), ("2026-11-05", 25.0, 25.5))
    m = mod.price_range_metrics(series, "2026-11-06", 26.0)
    assert m["status"] == "ok"
    assert m["coverage_3y_complete"] is False
    assert m["coverage_1y_complete"] is False
    assert m["low_3y"] == 20.0


def test_empty_series_is_unavailable_not_zero():
    m = mod.price_range_metrics([], "2026-11-06", 10.0)
    assert m["status"] == "UNAVAILABLE"
    assert "reason" in m
    assert "low_3y" not in m


def test_missing_price_leaves_distance_null():
    series = bars(("2026-01-05", 10.0, 10.5))
    m = mod.price_range_metrics(series, "2026-11-06", None)
    assert m["from_low_3y_pct"] is None and m["price"] is None


def test_load_bars_falls_back_to_ths(monkeypatch):
    def boom(item, as_of):
        raise RuntimeError("tencent down")

    def ths(item, as_of):
        return bars(("2026-01-05", 10.0, 10.5)), "ths-00-unadjusted"

    monkeypatch.setattr(mod, "fetch_tencent_raw", boom)
    monkeypatch.setattr(mod, "fetch_ths_raw", ths)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    series, source = mod.load_bars("600000.SH", "2026-11-06")
    assert source == "ths-00-unadjusted" and len(series) == 1


def test_load_bars_raises_when_every_source_is_empty(monkeypatch):
    monkeypatch.setattr(mod, "fetch_tencent_raw", lambda item, as_of: ([], "tencent"))
    monkeypatch.setattr(mod, "fetch_ths_raw", lambda item, as_of: ([], "ths"))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    with pytest.raises(RuntimeError):
        mod.load_bars("600000.SH", "2026-11-06")


def _payload():
    return {
        "data_as_of": "2026-11-06",
        "intersection": ["600000.SH", "000001.SZ"],
        "periods": {
            "week": [{"symbol": "600000.SH", "price": 12.0}, {"symbol": "000001.SZ", "price": 9.0}],
            "month": [],
            "quarter": [],
        },
        "enrichments": {"600000.SH": {}, "000001.SZ": {}},
    }


def test_attach_records_failure_without_fabricating(monkeypatch, tmp_path):
    payload = _payload()

    def fake_load(symbol, as_of):
        if symbol == "600000.SH":
            return bars(("2026-01-05", 6.0, 6.5)), "tencent-day-unadjusted"
        raise RuntimeError("upstream 502")

    monkeypatch.setattr(mod, "load_bars", fake_load)
    monkeypatch.setattr(mod, "DATA", tmp_path / "a-low-chip-stocks.json")
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    contract = mod.attach(payload, dry_run=True)

    ok = payload["enrichments"]["600000.SH"]["price_range"]
    bad = payload["enrichments"]["000001.SZ"]["price_range"]
    assert ok["status"] == "ok" and ok["low_3y"] == 6.0 and ok["adjustment"] == "none"
    assert bad["status"] == "UNAVAILABLE" and "upstream 502" in bad["reason"]
    assert contract["status"] == "degraded"
    assert contract["covered"] == 1 and contract["failed"] == ["000001.SZ"]
    assert contract["requested"] == 2


def test_attach_is_blocked_when_nothing_covers(monkeypatch, tmp_path):
    payload = _payload()

    def fake_load(symbol, as_of):
        raise RuntimeError("all dead")

    monkeypatch.setattr(mod, "load_bars", fake_load)
    monkeypatch.setattr(mod, "DATA", tmp_path / "a-low-chip-stocks.json")
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    contract = mod.attach(payload, dry_run=True)
    assert contract["status"] == "blocked" and contract["covered"] == 0


def test_dry_run_does_not_write(monkeypatch, tmp_path):
    target = tmp_path / "a-low-chip-stocks.json"
    original = json.dumps(_payload(), ensure_ascii=False)
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(mod, "DATA", target)
    monkeypatch.setattr(mod, "load_bars", lambda symbol, as_of: (bars(("2026-01-05", 5.0, 5.5)), "tencent-day-unadjusted"))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    mod.attach(_payload(), dry_run=True)
    assert target.read_text(encoding="utf-8") == original


def test_page_renders_three_price_range_metrics():
    page = PAGE.read_text(encoding="utf-8")
    for marker in ("1年最低", "3年最低", "距3年低", "priceRange: enr.price_range || null",
                   "priceRangeHtml(row.priceRange)", "parseJsonSafe(row.price_range)"):
        assert marker in page, marker
    assert "不复权" in page


def test_d1_worker_and_sync_carry_price_range():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "'price_range'" in worker
    assert "ADD COLUMN price_range TEXT" in worker
    assert "metric.price_range = parseJsonField(metric.price_range, null)" in worker
    assert "'price_range': enr.get('price_range')," in sync