"""底背离（直接 + 隔峰）信号契约测试。

覆盖：INTPART 向零取整、DIF 量级压缩（PDIFL/MDIF）、CROSS 严格语义、
直接/隔峰底背离的构造用例、底背离消失否定、attach 契约（ok/unavailable/
失败隔离/空池）、页面与 D1 单列通道接线。全程不联网、不读 sqlite 缓存。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_beili as mod  # noqa: E402
import beili_signal as sig  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.html"
PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"

AS_OF = "2026-11-13"


def _seg(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _bars(closes: list[float]) -> list[dict]:
    out, day = [], date(2026, 1, 5)
    for c in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({"trade_date": day.isoformat(), "open": c, "high": c * 1.01,
                    "low": c * 0.99, "close": c})
        day += timedelta(days=1)
    return out


def _direct_series() -> list[float]:
    """两段下跌、第二段更慢 → 价格更低而 DIF 抬高（直接底背离）。

    前面接一段无关的震荡，把长度顶到 MIN_BARS 以上，signal_at 才会返回 ok。
    """
    return (
        _seg(26, 24, 10) + _seg(24, 20, 20) + _seg(20, 22, 10)
        + _seg(20, 10, 30) + _seg(10, 13, 14) + _seg(13, 11, 16)
        + _seg(11, 12.5, 10) + _seg(12.5, 9.5, 18) + _seg(9.5, 9.8, 6)
    )


def _separated_series() -> list[float]:
    """三段结构：中间低点更高、最新低点最低 → 隔峰底背离。"""
    return (
        _seg(26, 20, 18) + _seg(20, 24, 12) + _seg(24, 20, 14)
        + _direct_series()
    )


# ---------- 通达信函数语义 ----------

def test_intpart_truncates_toward_zero():
    assert sig._intpart(3.9) == 3.0
    assert sig._intpart(-3.9) == -3.0
    assert sig._intpart(-0.2) == 0.0


def test_scale_prefix_keeps_two_significant_digits():
    assert sig._scale_prefix(0.35) == -1.0
    assert sig._scale_prefix(-0.35) == -1.0
    assert sig._scale_prefix(12.0) == 0.0
    assert sig._scale_prefix(0) is None


def test_scaled_matches_intpart_division():
    assert sig._scaled(-0.35, -1.0) == -3.0
    assert sig._scaled(0.35, -1.0) == 3.0
    assert sig._scaled(None, -1.0) is None
    assert sig._scaled(0.35, None) is None


def test_cross_up_is_strict():
    assert sig.cross_up([1.0, 2.0], [1.5, 1.5]) == [False, True]
    assert sig.cross_up([1.5, 2.0], [1.5, 1.5]) == [False, False]
    assert sig.cross_up([1.0, 1.5], [1.5, 1.5]) == [False, False]


# ---------- 背离判定 ----------

def test_direct_divergence_fires_on_lower_low_with_higher_dif():
    rows = sig.evaluate(_bars(_direct_series()))
    hits = [i for i, r in enumerate(rows) if r["direct_divergence"]]
    assert hits, "构造序列应产生直接底背离"
    for i in hits:
        row = rows[i]
        assert row["cl1"] < row["cl2"]
        assert row["macd_hist"] < 0 and rows[i - 1]["macd_hist"] < 0
        assert row["difl1"] > row["difl2"]


def test_separated_divergence_needs_three_segments():
    rows = sig.evaluate(_bars(_separated_series()))
    hits = [i for i, r in enumerate(rows) if r["separated_divergence"]]
    assert hits, "构造序列应产生隔峰底背离"
    for i in hits:
        row = rows[i]
        assert row["cl1"] < row["cl3"] < row["cl2"]
        assert row["difl1"] > row["difl3"]


def test_divergence_void_marks_broken_structure():
    rows = sig.evaluate(_bars(_direct_series()))
    assert any(r["divergence_void"] for r in rows), "构造序列应出现底背离消失"


def test_divergence_requires_negative_macd_hist():
    rows = sig.evaluate(_bars(_direct_series()))
    for i, row in enumerate(rows):
        if row["divergence"]:
            assert row["macd_hist"] < 0


# ---------- signal_at ----------

def test_signal_at_reports_hit_and_void():
    bars = _bars(_direct_series())
    result = sig.signal_at(bars, bars[-1]["trade_date"])
    assert result["status"] == "ok"
    assert result["today"] is True
    assert result["last_hit_date"] == bars[-1]["trade_date"]
    assert result["bars_since_hit"] == 0
    assert result["voided"] is False
    # 序列中段确实出现过「底背离消失」，说明否定条件被算到了
    assert any(r["divergence_void"] for r in sig.evaluate(bars))


def test_signal_at_unavailable_instead_of_no_signal():
    short = _bars(_direct_series()[: sig.MIN_BARS - 1])
    assert sig.signal_at(short, AS_OF)["status"] == "unavailable"
    bars = _bars(_direct_series())
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


def test_attach_marks_hit_when_recent_and_not_voided():
    bars = _bars(_direct_series())
    payload = _payload()
    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars)
    entry = payload["enrichments"]["600000.SH"]["beili"]
    assert entry["status"] == "ok"
    assert entry["hit"] is True and entry["today"] is True
    assert entry["bars_since_hit"] == 0
    assert entry["window"] == mod.WINDOW == 20
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["adjustment"] == "qfq"
    assert payload["enrichments"]["600000.SH"]["industry"] == "银行"


def test_attach_marks_no_hit_when_voided():
    bars = _bars(_direct_series())
    rows = sig.evaluate(bars)
    void_index = next(i for i, r in enumerate(rows) if r["divergence_void"])
    bars = bars[: void_index + 1]
    payload = _payload()
    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars)
    entry = payload["enrichments"]["600000.SH"]["beili"]
    assert entry["status"] == "ok"
    assert entry["voided"] is True
    assert entry["hit"] is False, "被消失条件否定的结构不算命中"
    assert contract["status"] == "ok"


def test_attach_isolates_single_symbol_failure():
    payload = _payload()
    bars = _bars(_direct_series())

    def loader(code):
        if code == "000001.SZ":
            raise RuntimeError("ths 502")
        return bars

    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=loader)
    missing = payload["enrichments"]["000001.SZ"]["beili"]
    assert missing["status"] == "UNAVAILABLE" and missing["hit"] is False
    assert "ths 502" in missing["reason"]
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

def test_page_wires_beili_filter_and_metric():
    page = PAGE.read_text(encoding="utf-8")
    for marker in (
        'data-filter="beili"',
        "底背离",
        "beili: enr.beili || null",
        "data-beili={row.beili?.hit ? '1' : '0'}",
        "beili: row.beili || row.beiliMetrics || null",
        "beili: parseJsonSafe(row.beili)",
        "activeFilters.has('beili') && c.dataset.beili !== '1'",
        "beiliHtml(bl)",
        "data-beili=",
    ):
        assert marker in page, marker


def test_d1_worker_and_sync_carry_beili():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "'beili'" in worker
    assert "ADD COLUMN beili TEXT" in worker
    assert "metric.beili = parseJsonField(metric.beili, null)" in worker
    assert "'beili': enr.get('beili')," in sync


def test_beili_only_channel_updates_single_column():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "const beiliOnly = preserveExisting && body?.beili_only === true;" in worker
    assert "beiliOnly ? ['beili'] :" in worker
    assert 'parser.add_argument("--beili-only"' in sync
    assert 'body["beili_only"] = True' in sync
    assert "beili_only=beili_only" in sync
