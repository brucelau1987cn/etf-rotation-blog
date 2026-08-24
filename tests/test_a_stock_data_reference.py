"""a-stock-data 参考抽取的单元测试（全 mock，不碰真实网络）。

覆盖：筹码 CYQ 三角分布算法 + 打板层解析逻辑。
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "scripts" / "reference" / "a-stock-data"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chip = _load("chip_distribution", REF / "chip_distribution.py")
limit = _load("limit_up", REF / "limit_up.py")


# ──────────────────────────────────────────────────────────────────────────
# 筹码 CYQ
# ──────────────────────────────────────────────────────────────────────────

def test_triangular_weights_one_price_board():
    """一字板：全部堆在一个价位。"""
    grid = np.linspace(0, 20, 21)
    w = chip._triangular_weights(grid, 10.0, 10.0, 10.0)
    assert w.sum() == pytest.approx(1.0)
    assert w[np.argmin(np.abs(grid - 10.0))] == pytest.approx(1.0)


def test_triangular_weights_normalizes_area():
    """正常三角分布面积归一，峰值在均价。"""
    grid = np.linspace(0, 20, 2001)
    w = chip._triangular_weights(grid, 8.0, 12.0, 10.5)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert np.argmax(w) == np.argmin(np.abs(grid - 10.5))


def test_triangular_weights_narrow_amplitude_fallback():
    """振幅窄于网格步长时兜底映射到最近网格点，不返回全零。"""
    grid = np.linspace(0, 20, 21)  # 步长 1.0
    w = chip._triangular_weights(grid, 10.01, 10.02, 10.015)  # 振幅 < 步长
    assert w.sum() > 0


def test_chip_distribution_missing_columns_raises():
    df = pd.DataFrame({"date": ["2026-01-01"], "close": [10.0]})
    with pytest.raises(ValueError, match="缺少列"):
        chip.chip_distribution(df)


def test_chip_distribution_first_day_seeds_full_float():
    """初始筹码播种为首日全部流通盘：单日窗口获利比例应为现价下方占比，而非 0/100 二值。"""
    df = pd.DataFrame([
        {"date": "2026-01-01", "high": 12.0, "low": 8.0, "close": 10.0, "turn": 5.0},
    ])
    r = chip.chip_distribution(df)
    # 现价 10 在区间中点附近，获利比例应在 0.5 上下（三角分布均价 (12+8+10)/3=10）
    assert 0.3 < r["profit_ratio"] < 0.7
    assert r["price"] == pytest.approx(10.0)


def test_chip_distribution_orders_by_date_internally():
    """乱序输入必须内部按 date 升序，否则换手衰减方向错。"""
    df = pd.DataFrame([
        {"date": "2026-01-05", "high": 11.0, "low": 9.0, "close": 10.5, "turn": 1.0},
        {"date": "2026-01-01", "high": 12.0, "low": 8.0, "close": 10.0, "turn": 5.0},
    ])
    r = chip.chip_distribution(df)
    assert r["price"] == pytest.approx(10.5)  # 现价取最后一根（date 最晚）


# ──────────────────────────────────────────────────────────────────────────
# 打板层解析
# ──────────────────────────────────────────────────────────────────────────

def test_fmt_zt_time():
    assert limit._fmt_zt_time(92500) == "09:25:00"
    assert limit._fmt_zt_time(145959) == "14:59:59"
    assert limit._fmt_zt_time(90000) == "09:00:00"


def test_anomaly_market_bj_by_code_prefix():
    """北交所 920 号段优先按代码判，不能被 m=0 误标成深市。"""
    assert limit._anomaly_market("920575", 0) == "BJ"
    assert limit._anomaly_market("430001", 0) == "BJ"
    assert limit._anomaly_market("600519", 1) == "SH"
    assert limit._anomaly_market("000001", 0) == "SZ"


def test_monitor_market_map_is_three_valued():
    """重点监控池 MARKET 是三值（含字母 B=北交所），不能当 0/1 二值。"""
    assert limit._MONITOR_MARKET["B"] == "BJ"
    assert limit._MONITOR_MARKET["1"] == "SH"
    assert limit._MONITOR_MARKET["0"] == "SZ"


def test_anomaly_rules_cover_bj():
    """异动规则码含北交所专用码 8。"""
    assert limit.ANOMALY_RULES[8] == "北交所连续10个交易日内3次出现同向异常波动"
