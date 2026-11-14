"""银龙（RSI9 上穿 20 + MACD 柱为负）信号契约测试。

覆盖：CROSS 语义、9 周期 RSI 递推、MACD<0 判据、观察窗、attach 契约
（ok/unavailable/失败隔离/空池）、页面与 D1 单列通道接线。全程不联网。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_yinlong as mod  # noqa: E402
import yinlong_signal as sig  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"

AS_OF = "2026-11-13"


def _dates(n):
    from datetime import date, timedelta

    out, day = [], date(2026, 1, 5)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _bars(closes):
    days = _dates(len(closes))
    return [
        {"trade_date": days[i], "open": c, "high": c * 1.01, "low": c * 0.99, "close": c}
        for i, c in enumerate(closes)
    ]


# ---------- 公式语义 ----------

def test_cross_up_is_strict_and_needs_both_sides():
    assert sig.cross_up([19.0, 21.0], 20.0) == [False, True]
    # 昨天正好等于 20：不算从下方向上穿
    assert sig.cross_up([20.0, 21.0], 20.0) == [False, False]
    # 今天不高于 20 不算
    assert sig.cross_up([19.0, 20.0], 20.0) == [False, False]
    # 一直在 20 上方不算（没有本次上穿）
    assert sig.cross_up([23.0, 30.0], 20.0) == [False, False]


def test_rsi_period_is_nine():
    assert sig.RSI_PERIOD == 9
    assert sig.RSI_LEVEL == 20.0


def test_xg_needs_negative_macd_hist():
    closes = [10.0] * 40 + [8.0, 7.5, 7.2, 7.0, 6.8, 6.6, 6.5, 6.4, 6.3, 6.35, 6.5, 6.9]
    rows = sig.evaluate(_bars(closes))
    last = rows[-1]
    # 单看 RSI 上穿与 MACD 符号的关系：XG 恒等于两者同时成立
    for row in rows:
        assert row["xg"] == (row["cross_up_20"] and row["macd_hist"] < 0)


def test_evaluate_marks_cross_up_on_rebound():
    """深跌后一根明显反弹会把 RSI9 推过 20。"""
    closes = [20.0] * 30 + [18.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.5, 11.0, 10.8, 12.5]
    rows = sig.evaluate(_bars(closes))
    assert rows[-1]["cross_up_20"] is True
    assert rows[-1]["rsi9"] > 20
    assert rows[-2]["rsi9"] < 20


def test_flat_price_keeps_rsi_undefined():
    """长期价格不变时分母为 0，RSI 应为 None 而不是 0/100。"""
    rows = sig.evaluate(_bars([10.0] * 70))
    assert rows[-1]["rsi9"] is None
    assert rows[-1]["xg"] is False


def test_signal_at_reports_window_and_staleness(monkeypatch):
    n = sig.MIN_BARS + 10
    days = _dates(n)
    rows = [
        {"trade_date": days[i], "close": 10.0, "rsi9": 25.0, "macd_hist": -0.1,
         "cross_up_20": False, "xg": i == sig.MIN_BARS + 7}
        for i in range(n)
    ]
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    result = sig.signal_at(_bars([10.0] * n), days[-1])
    assert result["status"] == "ok"
    assert result["last_hit_date"] == days[sig.MIN_BARS + 7]
    assert result["bars_since_hit"] == n - 1 - (sig.MIN_BARS + 7)
    assert result["xg"] is False


def test_signal_at_unavailable_instead_of_no_signal():
    assert sig.signal_at(_bars([10.0] * (sig.MIN_BARS - 1)), AS_OF)["status"] == "unavailable"
    result = sig.signal_at(_bars([10.0] * sig.MIN_BARS), "2030-12-31")
    assert result["status"] == "unavailable"
    assert result["reason"].startswith("no_bar_on")


# ---------- attach 契约 ----------

def _payload(codes=("600000.SH", "000001.SZ")):
    return {
        "data_as_of": AS_OF,
        "intersection": list(codes),
        "enrichments": {code: {"industry": "银行"} for code in codes},
    }


def _stub_rows(hit_index, n):
    days = _dates(n)
    return [
        {"trade_date": days[i], "close": 10.0, "rsi9": 25.0, "macd_hist": -0.2,
         "cross_up_20": False, "xg": i == hit_index}
        for i in range(n)
    ]


def test_attach_marks_hit_within_window(monkeypatch):
    n = sig.MIN_BARS + 10
    hit_index = n - 3
    rows = _stub_rows(hit_index, n)
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    payload = _payload()
    contract = mod.attach(payload, rows[-1]["trade_date"], bars_loader=lambda code: _bars([10.0] * n))
    entry = payload["enrichments"]["600000.SH"]["yinlong"]
    assert entry["status"] == "ok" and entry["hit"] is True
    assert entry["bars_since_hit"] == 2
    assert entry["window"] == mod.WINDOW == 20
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["adjustment"] == "qfq"
    assert payload["enrichments"]["600000.SH"]["industry"] == "银行"


def test_attach_marks_no_hit_when_stale(monkeypatch):
    n = sig.MIN_BARS + 10
    rows = _stub_rows(2, n)
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    payload = _payload()
    mod.attach(payload, rows[-1]["trade_date"], bars_loader=lambda code: _bars([10.0] * n))
    entry = payload["enrichments"]["600000.SH"]["yinlong"]
    assert entry["status"] == "ok" and entry["hit"] is False
    assert entry["bars_since_hit"] > mod.WINDOW


def test_attach_isolates_single_symbol_failure():
    payload = _payload()
    n = sig.MIN_BARS + 5

    def loader(code):
        if code == "000001.SZ":
            raise RuntimeError("ths 502")
        return _bars([10.0] * n)

    contract = mod.attach(payload, _dates(n)[-1], bars_loader=loader)
    missing = payload["enrichments"]["000001.SZ"]["yinlong"]
    assert missing["status"] == "UNAVAILABLE" and missing["hit"] is False
    assert "ths 502" in missing["reason"]
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}


def test_attach_all_failing_degrades_contract():
    payload = _payload()

    def loader(code):
        raise RuntimeError("network down")

    contract = mod.attach(payload, AS_OF, bars_loader=loader)
    assert contract["status"] == "unavailable"
    assert contract["coverage"]["ok"] == 0
    assert all(
        payload["enrichments"][code]["yinlong"]["status"] == "UNAVAILABLE"
        for code in payload["intersection"]
    )


def test_attach_empty_pool_short_circuits():
    payload = {"data_as_of": AS_OF, "intersection": [], "enrichments": {}}

    def loader(code):  # pragma: no cover
        raise AssertionError("loader must not run for an empty pool")

    contract = mod.attach(payload, AS_OF, bars_loader=loader)
    assert contract["status"] == "empty_pool"


# ---------- 页面 / D1 / sync 接线 ----------

def test_page_wires_yinlong_filter_and_metric():
    page = PAGE.read_text(encoding="utf-8")
    for marker in (
        'data-filter="yinlong"',
        "银龙信号",
        "yinlong: enr.yinlong || null",
        "data-yinlong={row.yinlong?.hit ? '1' : '0'}",
        "yinlong: row.yinlong || row.yinlongMetrics || null",
        "yinlong: parseJsonSafe(row.yinlong)",
        "activeFilters.has('yinlong') && c.dataset.yinlong !== '1'",
        "yinlongHtml(yl)",
        "data-yinlong=",
    ):
        assert marker in page, marker


def test_d1_worker_and_sync_carry_yinlong():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "'yinlong'" in worker
    assert "ADD COLUMN yinlong TEXT" in worker
    assert "metric.yinlong = parseJsonField(metric.yinlong, null)" in worker
    assert "'yinlong': enr.get('yinlong')," in sync


def test_yinlong_only_channel_updates_single_column():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "const yinlongOnly = preserveExisting && body?.yinlong_only === true;" in worker
    assert "yinlongOnly ? ['yinlong'] :" in worker
    assert 'parser.add_argument("--yinlong-only"' in sync
    assert 'body["yinlong_only"] = True' in sync
    assert "yinlong_only=yinlong_only" in sync
