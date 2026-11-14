"""三路金叉型背离（B1/B2/B3 底背离、D1/D2/D3 顶背离）契约测试。

覆盖：KDJ(9,3,3) 递推、BARSLAST(REF(X,1)) 语义、底/顶背离判定、观察窗、
attach 契约（ok/unavailable/失败隔离/空池）、页面与 D1 单列通道接线。
全程不联网、不读 sqlite 缓存。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_divergence_cross as mod  # noqa: E402
import divergence_cross_signal as sig  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"

AS_OF = "2026-11-13"


def _seg(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _bars(closes: list[float], spread: float = 0.01) -> list[dict]:
    out, day = [], date(2026, 1, 5)
    for c in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({"trade_date": day.isoformat(), "open": c,
                    "high": c * (1 + spread), "low": c * (1 - spread), "close": c})
        day += timedelta(days=1)
    return out


def _wave_series() -> list[float]:
    """多浪下跌：每浪更低，浪内反弹 → 反复出现金叉/死叉与背离。"""
    closes, p = [], 30.0
    for _ in range(7):
        low = p - 2.2
        closes += _seg(p, low, 14)
        closes += _seg(low, low + (p - low) * 0.45, 9)
        p = low - 0.3
    return closes


# ---------- 通达信函数语义 ----------

def test_kdj_matches_tdx_recursion():
    closes = _seg(20, 10, 40) + _seg(10, 16, 20)
    bars = _bars(closes)
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    k, d = sig.kdj(highs, lows, closes)
    assert len(k) == len(closes) == len(d)
    # 单调下跌时 RSV 贴 0，K/D 应低于 50；单调上涨后应抬升
    assert k[39] < 50
    assert k[-1] > k[39]
    assert all(0 <= v <= 100 for v in k)


def test_barslast_prev_excludes_current_bar():
    flags = [False, True, False, False, True, False]
    assert sig.barslast_prev(flags) == [None, None, 1, 2, 3, 1]


def test_cross_up_is_strict():
    assert sig.cross_up([1.0, 2.0], [1.5, 1.5]) == [False, True]
    assert sig.cross_up([1.5, 2.0], [1.5, 1.5]) == [False, False]


# ---------- 背离判定 ----------

def test_bottom_divergence_requires_lower_close_and_higher_indicator():
    closes = _wave_series()
    rows = sig.evaluate(_bars(closes))
    hits = [i for i, r in enumerate(rows) if r["bottom"]]
    assert hits, "构造序列应产生金叉型底背离"
    for i in hits:
        assert rows[i]["b1_macd"] or rows[i]["b2_kdj"] or rows[i]["b3_rsi"]


def test_top_divergence_requires_higher_close_and_lower_indicator():
    closes = list(reversed(_wave_series()))
    rows = sig.evaluate(_bars(closes))
    hits = [i for i, r in enumerate(rows) if r["top"]]
    assert hits, "镜像序列应产生金叉型顶背离"


def test_bottom_and_top_are_mutually_exclusive_on_same_bar():
    rows = sig.evaluate(_bars(_wave_series()))
    assert not any(r["bottom"] and r["top"] for r in rows)


def test_signal_at_reports_both_sides():
    bars = _bars(_wave_series())
    result = sig.signal_at(bars, bars[-1]["trade_date"])
    assert result["status"] == "ok"
    assert set(result["bottom"]) >= {"today", "last_hit_date", "bars_since_hit", "today_parts"}
    assert set(result["top"]) >= {"today", "last_hit_date", "bars_since_hit", "today_parts"}


def test_signal_at_unavailable_instead_of_no_signal():
    short = _bars(_wave_series()[: sig.MIN_BARS - 1])
    assert sig.signal_at(short, AS_OF)["status"] == "unavailable"
    bars = _bars(_wave_series())
    missing = sig.signal_at(bars, "2030-12-31")
    assert missing["status"] == "unavailable"
    assert missing["reason"].startswith("no_bar_on")


# ---------- attach 契约 ----------

def _payload(codes=("600000.SH", "000001.SZ")):
    return {
        "data_as_of": AS_OF,
        "intersection": list(codes),
        "enrichments": {code: {"industry": "银行"} for code in codes},
    }


def test_attach_writes_both_sides_and_contract():
    bars = _bars(_wave_series())
    payload = _payload()
    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars)
    entry = payload["enrichments"]["600000.SH"]
    assert entry["xdiv"]["status"] == "ok" and entry["xtop"]["status"] == "ok"
    assert entry["xdiv"]["window"] == mod.WINDOW == 5
    assert entry["xdiv"]["side"] == "bottom" and entry["xtop"]["side"] == "top"
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["adjustment"] == "qfq"
    assert payload["enrichments"]["600000.SH"]["industry"] == "银行"


def test_attach_hit_respects_window():
    bars = _bars(_wave_series())
    payload = _payload()
    mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars, window=5)
    entry = payload["enrichments"]["600000.SH"]["xdiv"]
    if entry["bars_since_hit"] is not None:
        assert entry["hit"] == (entry["bars_since_hit"] <= 5)


def test_attach_isolates_single_symbol_failure():
    bars = _bars(_wave_series())
    payload = _payload()

    def loader(code):
        if code == "000001.SZ":
            raise RuntimeError("ths 502")
        return bars

    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=loader)
    missing = payload["enrichments"]["000001.SZ"]
    assert missing["xdiv"]["status"] == "UNAVAILABLE"
    assert missing["xtop"]["status"] == "UNAVAILABLE"
    assert "ths 502" in missing["xdiv"]["reason"]
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}


def test_attach_all_failing_degrades_contract():
    payload = _payload()

    def loader(code):
        raise RuntimeError("network down")

    contract = mod.attach(payload, AS_OF, bars_loader=loader)
    assert contract["status"] == "unavailable"
    assert contract["coverage"]["ok"] == 0


def test_attach_empty_pool_short_circuits():
    payload = {"data_as_of": AS_OF, "intersection": [], "enrichments": {}}

    def loader(code):  # pragma: no cover
        raise AssertionError("loader must not run for an empty pool")

    assert mod.attach(payload, AS_OF, bars_loader=loader)["status"] == "empty_pool"


# ---------- 页面 / D1 / sync 接线 ----------

def test_page_wires_xdiv_and_xtop():
    page = PAGE.read_text(encoding="utf-8")
    for marker in (
        'data-filter="xdiv"',
        'data-filter="xtop"',
        "金叉背离",
        "顶背离",
        "xdiv: enr.xdiv || null",
        "xtop: enr.xtop || null",
        "data-xdiv={row.xdiv?.hit ? '1' : '0'}",
        "data-xtop={row.xtop?.hit ? '1' : '0'}",
        "xdiv: row.xdiv || row.xdivMetrics || null",
        "xtop: row.xtop || row.xtopMetrics || null",
        "xdiv: parseJsonSafe(row.xdiv)",
        "xtop: parseJsonSafe(row.xtop)",
        "activeFilters.has('xdiv') && c.dataset.xdiv !== '1'",
        "activeFilters.has('xtop') && c.dataset.xtop !== '1'",
        "xdivHtml(xd)",
        "xtopHtml(xt)",
    ):
        assert marker in page, marker


def test_d1_worker_and_sync_carry_xdiv_xtop():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    for col in ("xdiv", "xtop"):
        assert f"'{col}'" in worker
        assert f"ADD COLUMN {col} TEXT" in worker
        assert f"metric.{col} = parseJsonField(metric.{col}, null)" in worker
        assert f"'{col}': enr.get('{col}')," in sync
        assert f'parser.add_argument("--{col}-only"' in sync
        assert f'body["{col}_only"] = True' in sync
        assert f"{col}_only={col}_only" in sync
