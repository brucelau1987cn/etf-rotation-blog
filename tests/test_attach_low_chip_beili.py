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


# ---------- 截止日契约 ----------

def test_all_signals_warmup_is_as_of_and_future_bars_are_ignored(monkeypatch):
    import importlib
    for name in ("jinlong", "yinlong", "beili"):
        engine = importlib.import_module(name + "_signal")
        bars = _bars([10.0] * (engine.MIN_BARS + 15))
        early = engine.signal_at(bars, bars[engine.MIN_BARS - 2]["trade_date"])
        assert early["status"] == "unavailable", name
        assert early["reason"] == f"insufficient_bars:{engine.MIN_BARS - 1}/{engine.MIN_BARS}"
        # Non-flat history exercises EMA/RSI and segmented divergence state.
        bars = _bars(_separated_series())
        target = engine.MIN_BARS + 2
        full = engine.signal_at(bars, bars[target]["trade_date"])
        assert full == engine.signal_at(bars[:target + 1], bars[target]["trade_date"]), name
        assert full["history_bars"] == target + 1
        real_evaluate = engine.evaluate
        def prewarm_only(data, original=real_evaluate, field="divergence" if name == "beili" else "xg", minimum=engine.MIN_BARS):
            rows = original(data)
            for i, row in enumerate(rows):
                row[field] = i == minimum - 2
            return rows
        monkeypatch.setattr(engine, "evaluate", prewarm_only)
        assert engine.signal_at(bars)["last_hit_date"] is None, name


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


def test_vanish_ors_both_branches(monkeypatch):
    dif = [0, -2, -3, -2, -4, -5, -4, -1, -1.1, -4]
    monkeypatch.setattr(sig, "dif_dea", lambda closes: (dif, [d + 1 for d in dif]))
    monkeypatch.setattr(sig, "cross_up", lambda a, b: [i in (1, 4, 7) for i in range(10)])
    rows = sig.evaluate(_bars([20, 15, 14, 15, 18, 17, 18, 13, 12, 11]))
    assert rows[8]["direct_divergence"] and rows[8]["separated_divergence"]
    assert rows[9]["difl1"] > rows[9]["difl2"]
    assert rows[9]["difl1"] <= rows[9]["difl3"]
    assert rows[9]["divergence_void"] is True


def test_divergence_requires_negative_macd_hist():
    rows = sig.evaluate(_bars(_direct_series()))
    for i, row in enumerate(rows):
        if row["divergence"]:
            assert row["macd_hist"] < 0


# ---------- signal_at ----------

def test_frozen_threshold_delayed_invalidation_sticky_and_new_event(monkeypatch):
    bars = _bars([10.0] * (sig.MIN_BARS + 12))
    rows = sig.evaluate(bars)
    anchor = sig.MIN_BARS
    for row in rows:
        row.update(divergence=False, direct_divergence=False, separated_divergence=False,
                   divergence_void=False, dif=-1.0, difl1=-1.0, difl2=-2.0, difl3=-3.0)
    rows[anchor].update(divergence=True, direct_divergence=True, separated_divergence=True)
    # Moving segment references cannot move the event's frozen thresholds.
    for i in range(anchor + 1, len(rows)):
        rows[i].update(difl2=-20.0, difl3=-30.0)
    rows[anchor + 3]["dif"] = -2.0
    monkeypatch.setattr(sig, "evaluate", lambda data: rows[:len(data)])
    def state(i):
        return sig.signal_at(bars, bars[i]["trade_date"])
    assert state(anchor + 2)["active"] is True
    broken = state(anchor + 4)
    assert broken["voided"] is True
    assert broken["legacy_voided"] is False
    assert broken["invalidated_at"] == bars[anchor + 3]["trade_date"]
    assert broken["invalidation_reason"] == "direct_dif_threshold_broken"
    assert broken["anchor_reference_dif"] == {"direct": -2.0, "separated": -3.0}
    assert broken["anchor_date"] == bars[anchor]["trade_date"]
    assert broken["active"] is False and broken["structure_status"] == "voided"
    assert broken["model"].endswith("-v2")
    # New raw event can already have a broken within-segment trough.
    rows[anchor + 5].update(divergence=True, separated_divergence=True, difl1=-31.0)
    assert state(anchor + 5)["voided"] is True
    rows[anchor + 6].update(divergence=True, separated_divergence=True, difl1=-1.0)
    assert state(anchor + 6)["active"] is True
    assert state(anchor + 6)["invalidated_at"] is None
    assert state(anchor + 6)["anchor_reference_dif"] == {"separated": -30.0}
    # Equality on the separated branch invalidates and stays invalid on recovery.
    rows[anchor + 7]["dif"] = -30.0
    assert state(anchor + 8)["invalidation_reason"] == "separated_dif_threshold_broken"
    rows[anchor + 9].update(divergence=True, direct_divergence=True)
    rows[anchor + 10]["divergence_void"] = True
    assert state(anchor + 11)["legacy_voided"] is True
    assert state(anchor + 11)["invalidation_reason"] == "legacy_divergence_void"


def test_new_direct_anchor_resets_same_day_old_separated_void(monkeypatch):
    padding = sig.MIN_BARS
    tail_dif = [0, -2, -3, -2, -4, -5, -4, -1, -1.1, -4, -3.8, -5.1]
    dif = [0.0] * padding + tail_dif
    closes = [20.0] * padding + [20, 15, 14, 15, 18, 17, 18, 13, 12, 11, 11, 11]
    bars = _bars(closes)
    monkeypatch.setattr(sig, "dif_dea", lambda data: (dif[:len(data)], [d + 1 for d in dif[:len(data)]]))
    monkeypatch.setattr(sig, "cross_up", lambda a, b: [i in (padding + 1, padding + 4, padding + 7) for i in range(len(a))])
    rows = sig.evaluate(bars)
    old, new = padding + 8, padding + 9
    assert rows[old]["separated_divergence"] is True
    assert rows[new]["direct_divergence"] is True
    assert rows[new]["separated_divergence"] is False
    assert rows[new]["divergence_void"] is True
    assert rows[new]["difl2"] < rows[new]["difl1"] <= rows[new]["difl3"]
    previous = sig.signal_at(bars, bars[old]["trade_date"])
    assert previous["anchor_reference_dif"]["separated"] == -3
    for index in (new, new + 1):
        state = sig.signal_at(bars, bars[index]["trade_date"])
        assert state["anchor_date"] == bars[new]["trade_date"]
        assert state["anchor_reference_dif"] == {"direct": -5}
        assert state["active"] is True
        assert state["voided"] is False
        assert state["legacy_voided"] is False
        assert state["invalidated_at"] is None
        assert state["invalidation_reason"] is None
    broken = sig.signal_at(bars)
    assert broken["anchor_date"] == bars[new]["trade_date"]
    assert broken["voided"] is True
    assert broken["legacy_voided"] is False
    assert broken["invalidation_reason"] == "direct_dif_threshold_broken"


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


def test_attach_background_windows_are_inclusive_and_explicit(monkeypatch):
    import importlib
    for name in ("jinlong", "yinlong"):
        attach_mod = importlib.import_module("attach_low_chip_" + name)
        engine = importlib.import_module(name + "_signal")
        bars = _bars([10.0] * (engine.MIN_BARS + 30))
        original = engine.evaluate(bars)
        for distance in (0, 5, 6, 10, 11, 20, 21):
            rows = [dict(row, xg=i == len(bars) - 1 - distance) for i, row in enumerate(original)]
            monkeypatch.setattr(engine, "evaluate", lambda data: rows[:len(data)])
            payload = _payload(("600000.SH",))
            contract = attach_mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars)
            entry = payload["enrichments"]["600000.SH"][name]
            assert entry["hit"] == (distance <= 20)
            assert entry["recent_hit_5"] == (distance <= 5)
            assert entry["recent_hit_10"] == (distance <= 10)
            assert entry["window_semantics"] == contract["window_semantics"] == "bars_since_lte"
            assert contract["signal_semantics"] == "recent_event_background"
            assert entry["model"] == contract["model"] == attach_mod.MODEL
            assert entry["model"].endswith("-v2")
        missing = attach_mod._unavailable(AS_OF, "offline")
        assert missing["recent_hit_5"] is False and missing["recent_hit_10"] is False
        assert missing["window_semantics"] == "bars_since_lte"


def test_attach_carries_frozen_structure_state(monkeypatch):
    bars = _bars([10.0] * (sig.MIN_BARS + 6))
    rows = sig.evaluate(bars)
    for row in rows:
        row.update(divergence=False, direct_divergence=False, separated_divergence=False,
                   divergence_void=False, dif=-1.0, difl1=-1.0, difl2=-2.0)
    rows[-5].update(divergence=True, direct_divergence=True)
    rows[-2]["dif"] = -2.1
    monkeypatch.setattr(sig, "evaluate", lambda data: rows[:len(data)])
    payload = _payload(("600000.SH",))
    contract = mod.attach(payload, bars[-1]["trade_date"], bars_loader=lambda code: bars)
    entry = payload["enrichments"]["600000.SH"]["beili"]
    state = sig.signal_at(bars)
    assert entry["hit"] is False and entry["voided"] is True
    for key in ("legacy_voided", "invalidated_at", "invalidation_reason", "anchor_date",
                "anchor_reference_dif", "active", "structure_status", "model"):
        assert entry[key] == state[key]
    assert contract["window_semantics"] == "bars_since_lte"
    assert contract["signal_semantics"] == "recent_unvoided_structure"
    assert mod._unavailable(AS_OF, "offline")["structure_status"] == "unavailable"


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
