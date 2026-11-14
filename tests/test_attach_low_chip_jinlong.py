"""金龙（双龙抄底PRO）信号契约定型测试。

覆盖：通达信函数语义（MA/EMA/SMA/RSI/MACD/FILTER/COUNT）、启动1 三段条件、
VAR2 等价变形、观察窗命中判定、attach 契约（ok/unavailable/失败隔离/空池）、
页面与 D1 单列通道接线。全程不联网：行情 loader 全部注入。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_jinlong as mod  # noqa: E402
import jinlong_signal as sig  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"

AS_OF = "2026-11-13"


def _dates(n):
    """连续交易日日期序列（跳过周末），rows 与 bars 必须共用同一序列。"""
    from datetime import date, timedelta

    out, day = [], date(2026, 1, 5)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _bars(closes, *, amplitude=0.0, last_close=None):
    """按收盘价造日线：默认振幅 0，可用 amplitude 给每根统一放宽 H/L。"""
    bars = []
    for i, close in enumerate(closes):
        bar = {
            "trade_date": _dates(len(closes))[i],
            "open": close,
            "high": close * (1 + amplitude / 2),
            "low": close * (1 - amplitude / 2),
            "close": close,
        }
        bars.append(bar)
    if last_close is not None:
        bars[-1]["close"] = last_close
    return bars


# ---------- 通达信函数语义 ----------

def test_ma_returns_none_until_window_full():
    out = sig.ma([1, 2, 3, 4], 3)
    assert out == [None, None, 2.0, 3.0]


def test_ema_and_sma_seed_with_first_value():
    """通达信 EMA/SMA 都用首值播种，不是用前 N 根的均值。"""
    assert sig.ema([10, 10, 10], 9) == [10, 10, 10]
    assert sig.sma([2, 2, 2], 7, 1) == [2, 2, 2]
    # SMA(X,7,1) 的递推系数是 1/7
    got = sig.sma([7, 0], 7, 1)
    assert got[0] == 7 and got[1] == pytest.approx(6.0)


def test_rsi_of_monotonic_rise_is_100():
    out = sig.rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 7)
    assert out[0] is None
    assert out[-1] == pytest.approx(100.0)


def test_rsi_of_monotonic_fall_is_zero():
    out = sig.rsi(list(range(10, 0, -1)), 7)
    assert out[-1] == pytest.approx(0.0)


def test_filter_suppresses_signals_inside_window():
    flags = [True, True, False, False, False, False, True]
    assert sig.filter_signal(flags, 5) == [True, False, False, False, False, False, True]
    # 屏蔽期正好 5 根
    assert sig.filter_signal([True] + [False] * 4 + [True], 5) == [True, False, False, False, False, False]


def test_count_is_inclusive_window():
    flags = [True, False, True, False]
    assert sig.count_true(flags, 5) == [1, 1, 2, 2]
    assert sig.count_true(flags, 2) == [1, 1, 1, 1]


def test_macd_hist_matches_definition():
    closes = [10 + (i % 7) for i in range(80)]
    hist = sig.macd_hist(closes)
    dif = [a - b for a, b in zip(sig.ema(closes, 12), sig.ema(closes, 26))]
    dea = sig.ema(dif, 9)
    assert hist[-1] == pytest.approx(2 * (dif[-1] - dea[-1]))


def test_var2_equivalence():
    """VAR2>20 等价于 LOW < MA30 - 0.1*MA60（报告第二节的变形）。"""
    closes = [100.0] * 60 + [70.0]
    ma30, ma60 = sig.ma(closes, 30), sig.ma(closes, 60)
    low = 70.0
    var2 = ((ma30[-1] - low) / ma60[-1]) * 200
    assert var2 > 20
    assert low < ma30[-1] - 0.1 * ma60[-1]


# ---------- 启动1 三段条件 ----------

def test_start1_requires_deep_drop_amplitude_and_repetition():
    # 40 根平台 + 连续 5 根宽振幅下跌，最后一根 C/MA40 深度偏离
    closes = [100.0] * 40 + [95.0, 90.0, 85.0, 80.0, 76.0, 72.0, 70.0, 68.0, 66.0, 64.0]
    bars = _bars(closes, amplitude=0.08)
    rows = sig.evaluate(bars)
    assert rows[-1]["close"] / sig.ma(closes, 40)[-1] < sig.RATIO_DEEP_DROP
    assert rows[-1]["start1"] is True


def test_start1_false_without_amplitude():
    closes = [100.0] * 40 + [95.0, 90.0, 85.0, 80.0, 76.0, 72.0, 70.0, 68.0, 66.0, 64.0]
    rows = sig.evaluate(_bars(closes, amplitude=0.01))
    assert rows[-1]["start1"] is False


def test_start1_false_when_drop_is_shallow():
    closes = [100.0] * 40 + [99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0]
    rows = sig.evaluate(_bars(closes, amplitude=0.08))
    assert rows[-1]["start1"] is False


def test_xg_requires_band1_and_pick1_within_window():
    """XG = COUNT(选股1,13)>=1 AND 波段1；两者缺一即不入选。"""
    rows = sig.evaluate(_bars([100.0] * 60 + [70.0, 72.0], amplitude=0.06))
    assert all(not r["xg"] or (r["band1"] and r["pick1"]) for r in rows)


# ---------- signal_at 观察窗 ----------

def _rows_stub(xg_at):
    n = sig.MIN_BARS + 5
    days = _dates(n)
    return [
        {
            "trade_date": days[i],
            "close": 10.0, "start1": False, "gl1": False, "band1": False,
            "pick1": False, "xg": i in xg_at, "rsi7": 30.0, "var2": 40.0,
            "macd_hist": 0.0, "amplitude": 0.06,
        }
        for i in range(n)
    ]


def test_signal_at_reports_bars_since_last_hit(monkeypatch):
    hit_index = sig.MIN_BARS + 2          # 距最后一根 2 个交易日
    rows = _rows_stub({hit_index})
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    result = sig.signal_at(_bars([10.0] * len(rows)), rows[-1]["trade_date"])
    assert result["status"] == "ok"
    assert result["last_hit_date"] == rows[hit_index]["trade_date"]
    assert result["bars_since_hit"] == len(rows) - 1 - hit_index
    assert result["xg"] is False


def test_signal_at_flags_today_hit(monkeypatch):
    rows = _rows_stub({len(_rows_stub(set())) - 1})
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    result = sig.signal_at(_bars([10.0] * len(rows)), rows[-1]["trade_date"])
    assert result["xg"] is True and result["bars_since_hit"] == 0


def test_signal_at_reports_unavailable_instead_of_no_signal():
    short = _bars([10.0] * (sig.MIN_BARS - 1))
    assert sig.signal_at(short, AS_OF)["status"] == "unavailable"
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


def test_attach_marks_hit_within_window(monkeypatch):
    hit_index = sig.MIN_BARS + 2          # 距最后一根 2 个交易日，落在 20 日观察窗内
    rows = _rows_stub({hit_index})
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    payload = _payload()

    def loader(code):
        return _bars([10.0] * len(rows))

    contract = mod.attach(payload, rows[-1]["trade_date"], bars_loader=loader)
    entry = payload["enrichments"]["600000.SH"]["jinlong"]
    assert entry["status"] == "ok"
    assert entry["hit"] is True
    assert entry["bars_since_hit"] == len(rows) - 1 - hit_index
    assert entry["bars_since_hit"] <= mod.WINDOW
    assert entry["window"] == mod.WINDOW
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["window"] == mod.WINDOW
    assert contract["adjustment"] == "qfq"
    # 既有 enrichment 字段不被覆盖
    assert payload["enrichments"]["600000.SH"]["industry"] == "银行"


def test_attach_marks_no_hit_when_signal_is_stale(monkeypatch):
    rows = _rows_stub({5})
    monkeypatch.setattr(sig, "evaluate", lambda bars: rows)
    payload = _payload()

    def loader(code):
        return _bars([10.0] * len(rows))

    mod.attach(payload, rows[-1]["trade_date"], bars_loader=loader)
    entry = payload["enrichments"]["600000.SH"]["jinlong"]
    assert entry["status"] == "ok"
    assert entry["hit"] is False
    assert entry["last_hit_date"] == rows[5]["trade_date"]
    assert entry["bars_since_hit"] > mod.WINDOW


def test_attach_isolates_single_symbol_failure():
    payload = _payload()

    def loader(code):
        if code == "000001.SZ":
            raise RuntimeError("ths 502")
        return _bars([10.0] * sig.MIN_BARS)

    contract = mod.attach(payload, _dates(sig.MIN_BARS)[-1], bars_loader=loader)
    missing = payload["enrichments"]["000001.SZ"]["jinlong"]
    assert missing["status"] == "UNAVAILABLE"
    assert missing["hit"] is False
    assert "ths 502" in missing["reason"]
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}
    assert any("000001.SZ" in f for f in contract["failures"])


def test_attach_all_failing_degrades_contract():
    payload = _payload()

    def loader(code):
        raise RuntimeError("network down")

    contract = mod.attach(payload, AS_OF, bars_loader=loader)
    assert contract["status"] == "unavailable"
    assert contract["coverage"]["ok"] == 0
    assert all(
        payload["enrichments"][code]["jinlong"]["status"] == "UNAVAILABLE"
        for code in payload["intersection"]
    )


def test_attach_insufficient_bars_is_unavailable():
    payload = _payload()

    def loader(code):
        return _bars([10.0] * (sig.MIN_BARS - 10))

    contract = mod.attach(payload, _dates(sig.MIN_BARS - 10)[-1], bars_loader=loader)
    entry = payload["enrichments"]["600000.SH"]["jinlong"]
    assert entry["status"] == "UNAVAILABLE"
    assert "insufficient_bars" in entry["reason"]
    assert contract["status"] == "unavailable"


def test_attach_empty_pool_short_circuits():
    payload = {"data_as_of": AS_OF, "intersection": [], "enrichments": {}}

    def loader(code):  # pragma: no cover - 不得被调用
        raise AssertionError("loader must not run for an empty pool")

    contract = mod.attach(payload, AS_OF, bars_loader=loader)
    assert contract["status"] == "empty_pool"
    assert contract["coverage"]["requested"] == 0


def test_window_is_twenty_trading_days():
    """金龙信号极稀疏（257 只池近一年个位数事件），开关用 20 日观察窗。"""
    assert mod.WINDOW == 20


# ---------- 页面 / D1 / sync 接线 ----------

def test_page_wires_jinlong_filter_and_metric():
    page = PAGE.read_text(encoding="utf-8")
    for marker in (
        'data-filter="jinlong"',
        "金龙信号",
        "jinlong: enr.jinlong || null",
        "data-jinlong={row.jinlong?.hit ? '1' : '0'}",
        "jinlong: row.jinlong || row.jinlongMetrics || null",
        "jinlong: parseJsonSafe(row.jinlong)",
        "activeFilters.has('jinlong') && c.dataset.jinlong !== '1'",
        "jinlongHtml(jl)",
        "data-jinlong=",
    ):
        assert marker in page, marker


def test_page_filter_count_and_reset_behaviour():
    page = PAGE.read_text(encoding="utf-8")
    buttons = page.count('class="chip-filter-btn"')
    assert buttons == 11, "新增金龙开关后筛选按钮应为 11 个"


def test_d1_worker_and_sync_carry_jinlong():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "'jinlong'" in worker
    assert "ADD COLUMN jinlong TEXT" in worker
    assert "metric.jinlong = parseJsonField(metric.jinlong, null)" in worker
    assert "'jinlong': enr.get('jinlong')," in sync


def test_jinlong_only_channel_updates_single_column():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "const jinlongOnly = preserveExisting && body?.jinlong_only === true;" in worker
    assert "jinlongOnly ? ['jinlong'] : [" in worker
    assert 'parser.add_argument("--jinlong-only"' in sync
    assert 'body["jinlong_only"] = True' in sync
    assert "jinlong_only=jinlong_only" in sync


def test_worker_js_parses():
    """字符串断言全过但语法坏了会让 Pages Functions 构建失败；本地 astro
    build 不检查 functions/，必须真跑 node --check。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    result = subprocess.run([node, "--check", str(WORKER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
