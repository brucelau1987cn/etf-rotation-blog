"""韩精准制导 Python 复刻的口径回归测试。

关键契约（与 Pine 源码 docs/han-precise/TradingView_韩精准制导.pine 一致）：
- tdx_sma：通达信 SMA(X,N,M)，首值 = X，不能用 ta.sma / ta.rma 替代。
- tdx_filter：通达信 FILTER(X,N)，触发后屏蔽 N 根；被屏蔽条件不延长屏蔽期。
- 四色背景覆盖顺序：绿(1) < 橙(2) < 红(3) < 蓝(4)，红蓝互斥覆盖前两者。
- 状态四象限：K/D 交叉方向 × MACD 柱线正负，volume 必须 > 1 才有效。
- 数据缺失必须报错，不得静默降级或估算。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hp = _load("han_precise", "scripts/han_precise.py")


# --------------------------------------------------------------------------
# 通达信平滑函数
# --------------------------------------------------------------------------


def test_tdx_sma_initializes_with_first_value():
    out = hp.tdx_sma([10.0, 20.0, 30.0], 3, 1)
    # 递推 Y = (1*X + (N-1)*Yprev)/N
    assert out[0] == pytest.approx(10.0)
    assert out[1] == pytest.approx((1 * 20 + 2 * 10) / 3)
    assert out[2] == pytest.approx((1 * 30 + 2 * out[1]) / 3)


def test_tdx_sma_differs_from_simple_sma():
    src = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    tdx = hp.tdx_sma(src, 3, 1)
    simple = hp.sma(src, 3)
    assert not np.allclose(tdx[2:], simple[2:])


def test_tdx_sma_skips_nan_without_breaking_state():
    out = hp.tdx_sma([10.0, np.nan, 20.0], 3, 1)
    assert out[0] == pytest.approx(10.0)
    assert np.isnan(out[1])
    # na 输入不改变状态，下一根从 10 继续递推
    assert out[2] == pytest.approx((1 * 20 + 2 * 10) / 3)


def test_ema_matches_pine_alpha():
    src = [10.0, 12.0, 11.0, 13.0]
    out = hp.ema(src, 3)
    alpha = 2.0 / (3.0 + 1.0)
    # 前 length-1 根为 na，第 3 根（索引 2）用 SMA(length) 作为初值
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx((10.0 + 12.0 + 11.0) / 3.0)
    # 第 4 根按 alpha 递推
    assert out[3] == pytest.approx(alpha * 13.0 + (1.0 - alpha) * out[2])


# --------------------------------------------------------------------------
# 通达信 FILTER
# --------------------------------------------------------------------------


def test_filter_blocks_signals_within_window():
    flags = [False, True, True, True, False, True]
    out = hp.tdx_filter(flags, 2)
    # 索引 1 触发；2、3 被屏蔽；4 不触发；5 距 1 为 4 > 2，放行
    assert list(out) == [False, True, False, False, False, True]


def test_filter_blocked_signal_does_not_extend_window():
    # 索引 1 触发，索引 2/3 被屏蔽但不能重置计时；索引 4 应可触发
    flags = [False, True, True, True, True]
    out = hp.tdx_filter(flags, 2)
    assert list(out) == [False, True, False, False, True]


def test_filter_respects_exact_boundary():
    flags = [True, False, False, True]
    out = hp.tdx_filter(flags, 2)
    # 索引 3 距 0 为 3 > 2 → 放行；索引 2 距 0 为 2，不 > 2 → 屏蔽
    assert list(out) == [True, False, False, True]


# --------------------------------------------------------------------------
# 交叉 / 穿越
# --------------------------------------------------------------------------


def test_crossover_and_crossunder():
    a = [1.0, 2.0, 3.0]
    b = [2.0, 2.0, 2.0]
    assert list(hp.crossover(a, b)) == [False, False, True]
    assert list(hp.crossunder(a, b)) == [False, False, False]


def test_crossover_ignores_nan_pairs():
    # 当任意一侧的前值或当期值为 NaN 时，不产生交叉
    a = [np.nan, 2.0, np.nan, 3.0]
    b = [1.0, 1.0, 2.0, np.nan]
    assert not hp.crossover(a, b).any()


# --------------------------------------------------------------------------
# 四色背景
# --------------------------------------------------------------------------


def _frame(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    vols = volumes if volumes is not None else [1000.0] * n
    return pd.DataFrame(
        {
            "date": [f"2026-01-{i + 1:02d}" for i in range(n)],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols,
        }
    )


def test_background_green_below_ma89():
    # close < ma89 且 close/ma89 >= 0.90 为绿色(1)
    df = _frame([10.0] * 120 + [9.5] * 10)
    res = hp.compute(df, symbol="test")
    assert res.fields["background_id"] == 1


def test_background_red_when_ma89_ratio_above_110pct():
    # 前 120 根 10 元建立 MA89=10，随后暴涨到 12 → close/ma89 = 1.2 > 1.10
    df = _frame([10.0] * 120 + [12.0] * 5)
    res = hp.compute(df, symbol="test")
    assert res.fields["background_id"] == 3
    assert res.fields["close_over_ma89"] > 1.10


def test_background_blue_when_ma89_ratio_below_90pct():
    # 前 120 根 10 元建立 MA89=10，随后跌破到 8 → close/ma89 = 0.8 < 0.90
    df = _frame([10.0] * 120 + [8.0] * 5)
    res = hp.compute(df, symbol="test")
    assert res.fields["background_id"] == 4


def test_background_orange_above_ma89_without_10pct_gap():
    df = _frame([10.0] * 120 + [10.5] * 5)
    res = hp.compute(df, symbol="test")
    assert res.fields["background_id"] == 2


def test_background_blue_overrides_red_only_by_ratio():
    # 红色条件 (close/ma89>1.10) 与蓝色 (close/ma89<0.90) 互斥
    df = _frame([10.0] * 120 + [12.0] * 5)
    res = hp.compute(df, symbol="test")
    assert res.fields["background_id"] == 3
    assert res.fields["close_over_ma89"] > 1.10


# --------------------------------------------------------------------------
# 状态四象限
# --------------------------------------------------------------------------


def test_state_requires_valid_volume():
    df = _frame([10.0 + i * 0.1 for i in range(150)], volumes=[0.0] * 150)
    res = hp.compute(df, symbol="test")
    assert res.fields["state_id"] == 0
    assert res.state()[1] == "无状态"


def test_state_quadrant_mapping():
    # 构造 K>D 且 MACD>0 → 做多(1)
    up = [10.0] * 135 + [10.0 + (i + 1) * 0.5 for i in range(15)]
    res_up = hp.compute(_frame(up), symbol="test")
    assert res_up.fields["state_id"] == 1
    assert res_up.state()[1] == "做多"

    # 构造 K<D 且 MACD<0 → 做空(2)
    down = [20.0] * 135 + [20.0 - (i + 1) * 0.5 for i in range(15)]
    res_down = hp.compute(_frame(down), symbol="test")
    assert res_down.fields["state_id"] == 2
    assert res_down.state()[1] == "做空"


# --------------------------------------------------------------------------
# 交易信号
# --------------------------------------------------------------------------


def test_buy_signal_requires_5pct_single_day_gain():
    # 前 120 根横盘在 10，随后一天 +8% 且 close[1] < ma30
    closes = [10.0] * 120 + [10.8]
    df = _frame(closes)
    res = hp.compute(df, symbol="test")
    assert res.fields["day_change_pct"] == pytest.approx(8.0)
    assert res.fields["buy"] is True


def test_buy_signal_rejected_below_5pct():
    closes = [10.0] * 120 + [10.2]
    res = hp.compute(_frame(closes), symbol="test")
    assert res.fields["day_change_pct"] == pytest.approx(2.0)
    assert res.fields["buy"] is False


def test_filter_suppresses_repeated_buy_within_10_bars():
    # 连续多日 +8%：FILTER(…, 10) 只允许首个信号
    closes = [10.0] * 120
    price = 10.0
    for _ in range(12):
        price = price * 1.08
        closes.append(price)
    res = hp.compute(_frame(closes), symbol="test")
    buy_indices = [s["index"] for s in res.signals.get("买入", [])]
    assert len(buy_indices) == 1
    assert buy_indices[0] == 120


# --------------------------------------------------------------------------
# 数据契约
# --------------------------------------------------------------------------


def test_insufficient_bars_raises():
    with pytest.raises(ValueError, match="insufficient bars"):
        hp.compute(_frame([10.0] * 50), symbol="test")


def test_missing_column_raises():
    df = _frame([10.0] * 150).drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        hp.compute(df, symbol="test")


def test_last_index_limits_reported_signals():
    closes = [10.0] * 120 + [10.8]
    df = _frame(closes)
    res = hp.compute(df, symbol="test", last_index=120)
    assert res.fields["date"] == df["date"].iloc[120]
    assert res.last_index == 120


def test_report_contains_key_sections():
    closes = [10.0 + (i % 7) * 0.1 for i in range(150)]
    res = hp.compute(_frame(closes), symbol="sh603997")
    report = hp.format_report(res)
    assert "背景" in report
    assert "状态" in report
    assert "支撑" in report
    assert "韩精准制导" in report
