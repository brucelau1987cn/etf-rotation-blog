"""韩精准制导（TradingView Pine v6）的 Python 复刻。

用途：脱离 TradingView，在本地/云端对 A 股、ETF、港美股标的做
四色背景 + 四象限状态 + 交易信号（底部B / 高抛低吸 / 买入 / 加仓 /
减仓 / 清仓 / 强势狙击 / DX 买卖）的复算与实时提醒。

口径与 Pine 源码严格一致（见 docs/han-precise/TradingView_韩精准制导.pine）：
- 通达信 SMA(X,N,M)：Y=(M*X+(N-M)*Y前值)/N，首个有效值初始化为 X。
  与 ta.sma / ta.rma 的初始化和权重递推都不同，必须单独实现。
- 通达信 FILTER(X,N)：触发后屏蔽后续 N 根 K 线；被屏蔽的条件不延长
  屏蔽期（只有真正触发的信号才更新 lastSignalBar）。每个调用点独立状态。
- 均线切线角度：atan((MA/MA[1]-1)*100) 换算成度，区间 (-90, 90)。
- 背离判定全部使用「当根条件成立」的组合，不使用 Wilder 平滑。
- 背书：所有 na / 除零都显式处理，不静默降级；数据不可用时抛错。

本模块只做计算，不含网络与通知；数据获取见 han_precise_fetch.py。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, cast

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# 基础平滑函数
# --------------------------------------------------------------------------


def tdx_sma(src: Sequence[float] | pd.Series, length: int, weight: int) -> np.ndarray:
    """通达信 SMA(X,N,M)。首值 = X，递推 (M*X + (N-M)*Yprev)/N。"""
    values = np.asarray(src, dtype=float)
    out = np.full(values.shape, np.nan)
    state = np.nan
    for i, x in enumerate(values):
        if np.isnan(x):
            continue
        state = x if np.isnan(state) else (weight * x + (length - weight) * state) / length
        out[i] = state
    return out


def sma(src: Sequence[float] | pd.Series, length: int) -> np.ndarray:
    """ta.sma：简单移动平均，前 length-1 根为 na；窗口内含 na 时结果为 na。"""
    values = pd.Series(np.asarray(src, dtype=float))
    return values.rolling(length, min_periods=length).mean().to_numpy()


def ema(src: Sequence[float] | pd.Series, length: int) -> np.ndarray:
    """ta.ema：alpha = 2/(length+1)，前 length-1 根为 na。

    Pine 的 ta.ema 用 SMA(length) 作种子（不是首个值），这一点与通达信
    EMA、以及很多 Python 复刻（首值初始化）都不同，直接影响 MACD、DX、
    EMA15 支撑阻力和加速度判定，必须按 Pine 口径实现。
    """
    values = np.asarray(src, dtype=float)
    out = np.full(values.shape, np.nan)
    alpha = 2.0 / (length + 1.0)
    seed = sma(values, length)
    state = np.nan
    for i, x in enumerate(values):
        if np.isnan(x):
            continue
        if np.isnan(state):
            if not np.isnan(seed[i]):
                state = seed[i]
            else:
                continue
        else:
            state = alpha * x + (1.0 - alpha) * state
        out[i] = state
    return out


def tdx_filter(condition: Sequence[bool], length: int) -> np.ndarray:
    """通达信 FILTER(X,N)。信号之间至少间隔 N+1 根 K 线。"""
    flags = np.asarray(condition, dtype=bool)
    out = np.zeros(flags.shape, dtype=bool)
    last_signal = None
    for i, flag in enumerate(flags):
        if flag and (last_signal is None or (i - last_signal) > length):
            out[i] = True
            last_signal = i
    return out


def rolling_sum(flags: Sequence[bool], length: int) -> np.ndarray:
    """math.sum(cond ? 1 : 0, length)：前 length-1 根为 na（Pine 口径）。"""
    values = pd.Series(np.asarray(flags, dtype=float))
    return values.rolling(length, min_periods=length).sum().to_numpy()


def _nan_safe_window(values: np.ndarray, length: int, fn) -> np.ndarray:
    """ta.lowest / ta.highest 口径：窗口内忽略 na，全部 na 时结果为 na。"""
    arr = np.asarray(values, dtype=float)
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n < length:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(arr, length)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            reduced = fn(windows, axis=1)
    out[length - 1 :] = reduced
    return out


def rolling_max(values: Sequence[float], length: int) -> np.ndarray:
    return _nan_safe_window(np.asarray(values, dtype=float), length, np.nanmax)


def rolling_min(values: Sequence[float], length: int) -> np.ndarray:
    return _nan_safe_window(np.asarray(values, dtype=float), length, np.nanmin)


def _as_pair(a: Sequence[float], b: float | Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    a_arr = np.asarray(a, dtype=float)
    if isinstance(b, (int, float, np.floating, np.integer)):
        b_arr = np.full(a_arr.shape, float(b))
    else:
        b_arr = np.asarray(cast(Sequence[float], b), dtype=float)
    return a_arr, b_arr

def crossover(a: Sequence[float], b: float | Sequence[float]) -> np.ndarray:
    a_arr, b_arr = _as_pair(a, b)
    out = np.zeros(a_arr.shape, dtype=bool)
    for i in range(1, a_arr.shape[0]):
        prev_a, prev_b = a_arr[i - 1], b_arr[i - 1]
        if (
            np.isnan(prev_a)
            or np.isnan(prev_b)
            or np.isnan(a_arr[i])
            or np.isnan(b_arr[i])
        ):
            continue
        out[i] = prev_a <= prev_b and a_arr[i] > b_arr[i]
    return out


def crossunder(a: Sequence[float], b: float | Sequence[float]) -> np.ndarray:
    a_arr, b_arr = _as_pair(a, b)
    out = np.zeros(a_arr.shape, dtype=bool)
    for i in range(1, a_arr.shape[0]):
        prev_a, prev_b = a_arr[i - 1], b_arr[i - 1]
        if (
            np.isnan(prev_a)
            or np.isnan(prev_b)
            or np.isnan(a_arr[i])
            or np.isnan(b_arr[i])
        ):
            continue
        out[i] = prev_a >= prev_b and a_arr[i] < b_arr[i]
    return out


def value_when(condition: Sequence[bool], source: Sequence[float], occurrence: int = 0) -> np.ndarray:
    """ta.valuewhen：最近一次 condition 成立时 source 的取值。"""
    cond = np.asarray(condition, dtype=bool)
    src = np.asarray(source, dtype=float)
    out = np.full(src.shape, np.nan)
    state = np.nan
    for i in range(src.shape[0]):
        if cond[i] and not np.isnan(src[i]):
            state = src[i]
        out[i] = state
    return out


def safe_div(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(np.asarray(numerator).shape, np.nan)
    num = np.asarray(numerator, dtype=float)
    den = np.asarray(denominator, dtype=float)
    ok = ~np.isnan(den) & (den != 0.0)
    out[ok] = num[ok] / den[ok]
    return out


def rsv_series(df: pd.DataFrame, length: int) -> np.ndarray:
    """(close - lowest(low,N)) / (highest(high,N) - lowest(low,N)) * 100。"""
    lowest = rolling_min(df["low"].to_numpy(), length)
    highest = rolling_max(df["high"].to_numpy(), length)
    span = highest - lowest
    raw = safe_div(df["close"].to_numpy() - lowest, span) * 100.0
    raw[np.isnan(span) | (span == 0.0)] = np.nan
    return raw


def ma_angle(ma: np.ndarray) -> np.ndarray:
    """atan((MA/MA[1]-1)*100) 换算成度。"""
    prev = np.roll(ma, 1)
    prev[0] = np.nan
    ratio = safe_div(ma - prev, prev) * 100.0
    return np.degrees(np.arctan(ratio))


# --------------------------------------------------------------------------
# 结果结构
# --------------------------------------------------------------------------

BACKGROUND_COLORS = {
    1: ("绿色", (0, 100, 0)),
    2: ("橙色", (160, 80, 0)),
    3: ("红色", (120, 0, 0)),
    4: ("蓝色", (0, 0, 120)),
    0: ("无背景", None),
}

STATE_COLORS = {
    1: ("做多", "红色"),
    2: ("做空", "绿色"),
    3: ("强洗", "橙色"),
    4: ("弱升", "青色"),
    0: ("无状态", "灰色"),
}


@dataclass
class HanPreciseResult:
    symbol: str
    bars: pd.DataFrame
    last_index: int
    fields: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # -- 便捷读取 ---------------------------------------------------------
    @property
    def last(self) -> pd.Series:
        return self.bars.iloc[self.last_index]

    def value(self, key: str) -> Any:
        return self.fields.get(key)

    def background(self) -> tuple[int, str]:
        bid = int(self.fields["background_id"])
        return bid, BACKGROUND_COLORS[bid][0]

    def state(self) -> tuple[int, str, str]:
        sid = int(self.fields["state_id"])
        name, color = STATE_COLORS[sid]
        return sid, name, color

    def recent_signals(self, days: int = 10) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, items in self.signals.items():
            for item in items:
                if item["index"] >= self.last_index - days + 1:
                    out.append(item)
        out.sort(key=lambda x: (x["index"], x["name"]))
        return out


# --------------------------------------------------------------------------
# 主计算
# --------------------------------------------------------------------------


def compute(
    df: pd.DataFrame,
    symbol: str = "",
    last_index: int | None = None,
    min_bars: int = 120,
) -> HanPreciseResult:
    """按 Pine 源码口径逐根复算。

    df 需含 date/open/high/low/close/volume 列，按时间升序。
    last_index 用于盘中「用未收盘当根 K 线」做实时推演。
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if len(df) < min_bars:
        raise ValueError(f"insufficient bars: {len(df)} < {min_bars}")

    work = df.reset_index(drop=True).copy()
    close = work["close"].to_numpy(dtype=float)
    open_ = work["open"].to_numpy(dtype=float)
    high = work["high"].to_numpy(dtype=float)
    low = work["low"].to_numpy(dtype=float)
    volume = work["volume"].to_numpy(dtype=float)
    n = len(work)

    # 一、均线 / 角度 / 速度 ----------------------------------------------
    ma03 = sma(close, 3)
    ma05 = sma(close, 5)
    ma10 = sma(close, 10)
    ma18 = sma(close, 18)
    ma20 = sma(close, 20)
    ma30 = sma(close, 30)
    ma89 = sma(close, 89)

    bias05 = safe_div(close - ma05, ma05) * 100.0
    bias30 = safe_div(close - ma30, ma30) * 100.0

    bb05 = ma_angle(ma05)
    bb10 = ma_angle(ma10)
    bb30 = ma_angle(ma30)

    speed_input = ema(safe_div(ma05 - np.roll(ma05, 1), np.roll(ma05, 1)), 3) * 100.0
    speed05 = tdx_sma(speed_input, 3, 1)
    speed05_prev = np.roll(speed05, 1)
    speed05_prev[0] = np.nan
    acceleration05 = ema(speed05 - speed05_prev, 3)

    angle_cross_60 = crossover(bb05, 60.0)
    angle_cross_30 = crossover(bb05, 30.0)
    angle_cross_down_30 = crossunder(bb05, 30.0)
    bias_cross_down_0 = crossunder(bias05, 0.0)
    bias_cross_up_0 = crossover(bias05, 0.0)

    angle_up_count = rolling_sum(angle_cross_30, 5)
    angle_down_count = rolling_sum(angle_cross_down_30, 5)
    bias_up_count = rolling_sum(bias_cross_up_0, 3)

    ma05_prev = np.roll(ma05, 1)
    ma10_prev = np.roll(ma10, 1)
    bias30_prev = np.roll(bias30, 1)
    ma10_prev3 = np.roll(ma10, 3)
    close_prev = np.roll(close, 1)
    close_prev[0] = np.nan
    ma05_prev[0] = np.nan
    ma10_prev[0] = np.nan
    bias30_prev[0] = np.nan
    ma10_prev3[:3] = np.nan

    sniper_raw = (bb30 > 30.0) & (bb10 > 45.0) & angle_cross_60
    add_raw = (
        (angle_up_count >= 1.0)
        & (ma05 > ma05_prev)
        & (bias30 > bias30_prev)
        & (ma10 > ma10_prev)
        & (acceleration05 > np.roll(acceleration05, 1))
        & (speed05 > speed05_prev)
    )
    clear_raw = (
        (angle_down_count >= 1.0)
        & ((close > ma30) | (open_ > ma30))
        & (ma05 < ma05_prev)
        & (bias30 < bias30_prev)
        & (ma10 < ma10_prev)
    )
    reduce_raw = bias_cross_down_0 & (bias30 < bias30_prev) & (ma10 > ma10_prev)
    day_change_pct = safe_div(close - close_prev, close_prev) * 100.0
    buy_raw = (
        (bias_up_count >= 1.0)
        & (bias30 > bias30_prev)
        & (ma05 > ma05_prev)
        & (day_change_pct > 5.0)
        & (close_prev < ma30)
    )

    sniper = tdx_filter(sniper_raw, 10)
    add_position = tdx_filter(add_raw, 10)
    clear_position = tdx_filter(clear_raw, 10)
    reduce_position = tdx_filter(reduce_raw, 10)
    buy_position = tdx_filter(buy_raw, 10)

    # 二、DX ---------------------------------------------------------------
    momentum = close - close_prev
    dx_numerator = ema(ema(momentum, 6), 6)
    dx_denominator = ema(ema(np.abs(momentum), 6), 6)
    dx = 100.0 * safe_div(dx_numerator, dx_denominator)
    dx_ma2 = sma(dx, 2)
    dx_low2 = rolling_min(dx, 2)
    dx_low7 = rolling_min(dx, 7)
    dx_high2 = rolling_max(dx, 2)
    dx_high7 = rolling_max(dx, 7)
    dx_negative_count = rolling_sum(dx < 0.0, 2)
    dx_high_count = rolling_sum(dx > 50.0, 2)
    dx_cross_up = crossover(dx, dx_ma2)
    dx_cross_down = crossunder(dx, dx_ma2)
    price_cross_ma18 = crossover(close, ma18)
    dx_buy = (dx_low2 == dx_low7) & (dx_negative_count > 0.0) & dx_cross_up
    dx_add = (ma18 >= np.roll(ma18, 3)) & price_cross_ma18
    dx_sell = (dx_high2 == dx_high7) & (dx_high_count > 0.0) & dx_cross_down

    # 三、K/D + MACD 四象限状态 -------------------------------------------
    rsv09 = rsv_series(work, 9)
    k = tdx_sma(rsv09, 3, 1)
    d = tdx_sma(k, 3, 1)
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    macd = 2.0 * (dif - dea)
    volume_valid = (~np.isnan(volume)) & (volume > 1.0)
    long_state = (k > d) & (macd > 0.0) & volume_valid
    short_state = (k < d) & (macd < 0.0) & volume_valid
    wash_state = (k < d) & (macd > 0.0) & volume_valid
    rise_state = (k > d) & (macd < 0.0) & volume_valid
    state_id = np.zeros(n, dtype=int)
    state_id[long_state] = 1
    state_id[short_state] = 2
    state_id[wash_state] = 3
    state_id[rise_state] = 4
    tyx4 = sma((open_ + high + low + close) / 4.0, 13)
    strong_long = long_state & (close > tyx4) & (close > open_)
    strong_short = short_state & (close < tyx4) & (close < open_)

    # 四、四色背景（严格按原文件覆盖顺序：绿 < 橙 < 红 < 蓝） -------------
    close_over_ma03 = safe_div(close, ma03)
    close_over_ma89 = safe_div(close, ma89)
    background_id = np.zeros(n, dtype=int)
    bg_mask = close < ma89
    background_id[bg_mask] = 1
    bg_mask = (close > ma89) & (close_over_ma03 < 1.10)
    background_id[bg_mask] = 2
    bg_mask = close_over_ma89 > 1.10
    background_id[bg_mask] = 3
    bg_mask = close_over_ma89 < 0.90
    background_id[bg_mask] = 4

    # 五、支撑 / 压力 / EMA15 锚点 ---------------------------------------
    highest_close30 = rolling_max(close, 30)
    lowest_close30 = rolling_min(close, 30)
    pressure = sma(np.roll(highest_close30, 1), 2)
    support = sma(np.roll(lowest_close30, 1), 2)
    ema15 = ema(close, 15)
    ema15_prev = np.roll(ema15, 1)
    ema15_prev[0] = np.nan
    ema15_slope = safe_div(ema15 - ema15_prev, ema15_prev) * 100.0
    ema15_turn_up = crossover(ema15_slope, 0.0)
    ema15_turn_down = crossunder(ema15_slope, 0.0)
    support_anchor = value_when(ema15_turn_up, ema15_prev)
    resistance_anchor = value_when(ema15_turn_down, ema15_prev)
    ema_support = np.where(ema15_slope >= 0.0, support_anchor, np.nan)
    ema_resistance = np.where(ema15_slope < 0.0, resistance_anchor, np.nan)

    # 六、X_TT8 底部 B ------------------------------------------------------
    x_rsv43 = rsv_series(work, 43)
    x_aa2 = tdx_sma(x_rsv43, 2, 1)
    x_trend = tdx_sma(x_aa2, 2, 1)
    macd_prev1 = np.roll(macd, 1)
    macd_prev2 = np.roll(macd, 2)
    close_prev2 = np.roll(close, 2)
    close_prev3 = np.roll(close, 3)
    for arr in (macd_prev1, macd_prev2, close_prev2, close_prev3):
        arr[:3] = np.nan
    x_tt1 = (macd > 0.0) & (macd < macd_prev1) & (macd_prev1 >= macd_prev2)
    x_tt2 = (close < close_prev) & (close_prev >= close_prev2) & (close_prev2 >= close_prev3)
    x_tt3 = (x_trend > 89.0) & (x_trend <= np.roll(x_trend, 1))
    x_top = x_tt3 & x_tt1 & x_tt2
    x_tt5 = (macd < 0.0) & (macd > macd_prev1) & (macd_prev1 <= macd_prev2)
    x_tt6 = (close >= close_prev) & (close_prev <= close_prev2) & (close_prev2 <= close_prev3)
    x_tt7 = (x_trend < 11.0) & (x_trend >= np.roll(x_trend, 1))
    x_bottom = x_tt7 & x_tt5 & x_tt6

    # 七、GDX_XH 高抛低吸 ---------------------------------------------------
    gdx_rsv27 = rsv_series(work, 27)
    gdx_sma27 = tdx_sma(gdx_rsv27, 5, 1)
    gdx_smooth27 = tdx_sma(gdx_sma27, 3, 1)
    gdx_var1 = 3.0 * gdx_sma27 - 2.0 * gdx_smooth27
    gdx_var2 = ema(gdx_var1, 8)
    gdx_rsv55 = rsv_series(work, 55)
    gdx_sma55 = tdx_sma(gdx_rsv55, 5, 1)
    gdx_smooth55 = tdx_sma(gdx_sma55, 3, 1)
    gdx_var3 = 3.0 * gdx_sma55 - 2.0 * gdx_smooth55
    gdx_qs = ema(gdx_var3, 3) - 10.0
    gdx_cross = crossover(gdx_qs, gdx_var2)
    gdx_signal = gdx_cross & (gdx_qs > np.roll(gdx_qs, 1)) & (gdx_qs < 50.0)

    # 汇总 -----------------------------------------------------------------
    idx = n - 1 if last_index is None else int(last_index)
    if idx < 0 or idx >= n:
        raise IndexError(f"last_index out of range: {idx}")

    dates = work["date"].astype(str).to_numpy() if "date" in work.columns else np.array(
        [str(i) for i in range(n)]
    )

    fields: dict[str, Any] = {
        "date": dates[idx],
        "close": float(close[idx]),
        "open": float(open_[idx]),
        "high": float(high[idx]),
        "low": float(low[idx]),
        "volume": float(volume[idx]),
        "ma03": float(ma03[idx]),
        "ma05": float(ma05[idx]),
        "ma10": float(ma10[idx]),
        "ma18": float(ma18[idx]),
        "ma20": float(ma20[idx]),
        "ma30": float(ma30[idx]),
        "ma89": float(ma89[idx]),
        "bias05": float(bias05[idx]),
        "bias30": float(bias30[idx]),
        "bb05": float(bb05[idx]),
        "bb10": float(bb10[idx]),
        "bb30": float(bb30[idx]),
        "speed05": float(speed05[idx]),
        "acceleration05": float(acceleration05[idx]),
        "day_change_pct": float(day_change_pct[idx]),
        "k": float(k[idx]),
        "d": float(d[idx]),
        "dif": float(dif[idx]),
        "dea": float(dea[idx]),
        "macd": float(macd[idx]),
        "state_id": int(state_id[idx]),
        "strong_long": bool(strong_long[idx]),
        "strong_short": bool(strong_short[idx]),
        "background_id": int(background_id[idx]),
        "close_over_ma89": float(close_over_ma89[idx]),
        "close_over_ma03": float(close_over_ma03[idx]),
        "support": float(support[idx]),
        "pressure": float(pressure[idx]),
        "ema15": float(ema15[idx]),
        "ema15_slope": float(ema15_slope[idx]),
        "ema_support": None if np.isnan(ema_support[idx]) else float(ema_support[idx]),
        "ema_resistance": None if np.isnan(ema_resistance[idx]) else float(ema_resistance[idx]),
        "x_trend": float(x_trend[idx]),
        "gdx_var2": float(gdx_var2[idx]),
        "gdx_qs": float(gdx_qs[idx]),
        "dx": float(dx[idx]),
        "dx_ma2": float(dx_ma2[idx]),
        "sniper": bool(sniper[idx]),
        "add": bool(add_position[idx]),
        "clear": bool(clear_position[idx]),
        "reduce": bool(reduce_position[idx]),
        "buy": bool(buy_position[idx]),
        "dx_buy": bool(dx_buy[idx]),
        "dx_add": bool(dx_add[idx]),
        "dx_sell": bool(dx_sell[idx]),
        "x_bottom": bool(x_bottom[idx]),
        "x_top": bool(x_top[idx]),
        "gdx_signal": bool(gdx_signal[idx]),
    }

    signals: dict[str, list[dict[str, Any]]] = {}
    for mask, name, extra in (
        (x_bottom, "底部B", {"x_trend": x_trend, "macd": macd}),
        (gdx_signal, "高抛低吸", {"gdx_qs": gdx_qs, "gdx_var2": gdx_var2}),
        (buy_position, "买入", {"day_change_pct": day_change_pct}),
        (add_position, "加仓", {"speed05": speed05}),
        (reduce_position, "减仓", {"bias05": bias05}),
        (clear_position, "清仓", {"bias30": bias30}),
        (sniper, "强势狙击", {"bb30": bb30, "bb10": bb10}),
        (dx_buy, "DX买", {"dx": dx}),
        (dx_add, "DX加1", {"dx": dx}),
        (dx_sell, "DX卖1", {"dx": dx}),
    ):
        items: list[dict[str, Any]] = []
        for i in np.flatnonzero(mask):
            if i > idx:
                continue
            item: dict[str, Any] = {
                "name": name,
                "index": int(i),
                "date": str(dates[i]),
                "close": float(close[i]),
            }
            for key, arr in (extra or {}).items():
                val = arr[i]
                item[key] = bool(val) if isinstance(val, (bool, np.bool_)) else float(val)
            items.append(item)
        if items:
            signals[name] = items

    result = HanPreciseResult(symbol=symbol, bars=work, last_index=idx, fields=fields, signals=signals)
    return result


def format_report(result: HanPreciseResult, recent_days: int = 10) -> str:
    f = result.fields
    bg_id, bg_name = result.background()
    st_id, st_name, st_color = result.state()

    def fmt(key: str, digits: int = 2, suffix: str = "") -> str:
        val = f.get(key)
        if val is None:
            return "—"
        if isinstance(val, float):
            return f"{val:.{digits}f}{suffix}"
        return str(val)

    lines = [
        f"【韩精准制导】{result.symbol} {f['date']}",
        f"收盘 {fmt('close')}  涨跌幅 {fmt('day_change_pct', 2, '%')}",
        f"背景：{bg_name}（MA89 {fmt('ma89')} / MA03 {fmt('ma03')}）",
        f"状态：{st_name}（{st_color}）  K {fmt('k')}  D {fmt('d')}  MACD {fmt('macd')}",
        f"角度 BB05 {fmt('bb05')}  BB10 {fmt('bb10')}  BB30 {fmt('bb30')}",
        f"乖离 BIAS05 {fmt('bias05', 2, '%')}  BIAS30 {fmt('bias30', 2, '%')}",
        f"支撑 {fmt('support')}  压力 {fmt('pressure')}",
        f"EMA15 支撑 {fmt('ema_support')}  阻力 {fmt('ema_resistance')}",
        f"X_TREND {fmt('x_trend')}  GDX_QS {fmt('gdx_qs')}  DX {fmt('dx')}",
    ]
    recent = result.recent_signals(recent_days)
    if recent:
        lines.append("近期信号：")
        for item in recent:
            lines.append(f"  {item['date']} {item['name']} @ {item['close']}")
    else:
        lines.append("近期信号：无")
    return "\n".join(lines)


def fetch_bars(
    symbol: str,
    count: int = 250,
    timeout: int = 15,
    include_realtime_tick: bool = True,
) -> pd.DataFrame:
    """拉取前复权日K线数据，支持双 host 自动容错与实时 Tick 增补。"""
    import json
    import urllib.request

    hosts = ["ifzq.gtimg.cn", "web.ifzq.gtimg.cn"]
    rows = None
    last_err = None
    for host in hosts:
        url = f"https://{host}/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            node = (payload.get("data") or {}).get(symbol) or {}
            rows = node.get("qfqday") or node.get("day") or []
            if rows:
                break
        except Exception as e:
            last_err = e
            continue
    if not rows:
        raise RuntimeError(f"failed to fetch bars for {symbol} on all hosts: {last_err}")

    frame = pd.DataFrame(
        [r[:6] for r in rows],
        columns=["date", "open", "close", "high", "low", "volume"],
    )
    for col in ("open", "close", "high", "low", "volume"):
        frame[col] = frame[col].astype(float)

    if include_realtime_tick:
        try:
            q_url = f"http://qt.gtimg.cn/q={symbol}"
            q_req = urllib.request.Request(q_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(q_req, timeout=5) as q_resp:
                q_text = q_resp.read().decode("gbk", errors="ignore")
            p = q_text.split("~")
            if len(p) > 35 and p[30]:
                raw_time = p[30]
                q_date = f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}"
                cur_price = float(p[3])
                cur_open = float(p[5])
                cur_vol = float(p[6])
                cur_high = float(p[33])
                cur_low = float(p[34])
                if cur_price > 0 and cur_vol > 0:
                    last_date = frame["date"].iloc[-1]
                    if q_date > last_date:
                        new_row = pd.DataFrame(
                            [
                                {
                                    "date": q_date,
                                    "open": cur_open,
                                    "close": cur_price,
                                    "high": cur_high,
                                    "low": cur_low,
                                    "volume": cur_vol,
                                }
                            ]
                        )
                        frame = pd.concat([frame, new_row], ignore_index=True)
                    elif q_date == last_date:
                        frame.iloc[-1, frame.columns.get_loc("close")] = cur_price
                        frame.iloc[-1, frame.columns.get_loc("high")] = max(
                            frame["high"].iloc[-1], cur_high
                        )
                        frame.iloc[-1, frame.columns.get_loc("low")] = min(
                            frame["low"].iloc[-1], cur_low
                        )
                        frame.iloc[-1, frame.columns.get_loc("volume")] = cur_vol
        except Exception:
            pass

    return frame


if __name__ == "__main__":  # pragma: no cover - 手工自检入口
    import argparse

    parser = argparse.ArgumentParser(description="韩精准制导 Python 复刻")
    parser.add_argument("symbol", help="如 sh603997 / sz300750 / sh511260")
    parser.add_argument("--count", type=int, default=250)
    args = parser.parse_args()

    frame = fetch_bars(args.symbol, count=args.count)
    out = compute(frame, symbol=args.symbol)
    print(format_report(out))
