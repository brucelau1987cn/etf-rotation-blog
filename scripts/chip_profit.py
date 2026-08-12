import math
from collections import deque

import numpy as np
import pandas as pd


def effective_turnover(
    value,
    is_percent=True,
    mode="linear",
):
    """将换手率转换成旧筹码被替换的比例。"""
    if pd.isna(value):
        return 0.0
    turnover = float(value)
    if is_percent:
        turnover /= 100.0
    turnover = max(turnover, 0.0)
    if mode == "linear":
        return min(turnover, 1.0)
    if mode == "poisson":
        return 1.0 - math.exp(-turnover)
    raise ValueError("mode必须是'linear'或'poisson'")


def triangular_price_weights(price_grid, low, high, peak):
    """把当天成交筹码近似分配到最低价至最高价之间（三角分布，峰值=成交均价）。"""
    low = float(low)
    high = float(high)
    peak = float(peak)
    if high < low:
        low, high = high, low
    weights = np.zeros(price_grid.size, dtype=float)
    if high <= low:
        index = np.argmin(np.abs(price_grid - peak))
        weights[index] = 1.0
        return weights
    mask = (price_grid >= low) & (price_grid <= high)
    prices = price_grid[mask]
    if prices.size == 0:
        index = np.argmin(np.abs(price_grid - peak))
        weights[index] = 1.0
        return weights
    price_span = high - low
    epsilon = max(price_span * 1e-9, np.finfo(float).eps)
    peak = np.clip(peak, low + epsilon, high - epsilon)
    left_weights = (prices - low) / max(peak - low, epsilon)
    right_weights = (high - prices) / max(high - peak, epsilon)
    day_weights = np.where(prices <= peak, left_weights, right_weights)
    day_weights = np.clip(day_weights, 0.0, None)
    if day_weights.sum() <= 0:
        if price_grid.size > 1:
            grid_step = price_grid[1] - price_grid[0]
        else:
            grid_step = price_span
        sigma = max(price_span / 6.0, grid_step)
        day_weights = np.exp(-0.5 * ((prices - peak) / sigma) ** 2)
    if day_weights.sum() <= 0:
        index = np.argmin(np.abs(price_grid - peak))
        weights[index] = 1.0
    else:
        weights[mask] = day_weights / day_weights.sum()
    return weights


def profit_ratio(chip_distribution, price_grid, close_price):
    """成本小于或等于收盘价的筹码占比。"""
    total_chips = float(chip_distribution.sum())
    if total_chips <= 0:
        return np.nan
    position = np.searchsorted(price_grid, float(close_price), side="right")
    profitable_chips = chip_distribution[:position].sum()
    return float(profitable_chips / total_chips)


def calculate_chip_profit(
    bars,
    bins=2400,
    turnover_col="turnover_rate",
    turnover_is_percent=True,
    avg_price_col="avg_price",
    turnover_mode="linear",
    recent_windows=(1, 5, 20),
):
    """计算筹码获利盘。返回 daily/weekly/monthly 三套结果。"""
    required_columns = {"date", "high", "low", "close", turnover_col}
    missing_columns = required_columns.difference(bars.columns)
    if missing_columns:
        raise ValueError(f"缺少字段：{sorted(missing_columns)}")
    data = bars.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    numeric_columns = ["high", "low", "close", turnover_col]
    if avg_price_col in data.columns:
        numeric_columns.append(avg_price_col)
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["high", "low", "close"]].isna().any().any():
        raise ValueError("最高价、最低价或收盘价存在无效值")
    if (data["low"] <= 0).any():
        raise ValueError("价格必须大于0")
    if (data["high"] < data["low"]).any():
        raise ValueError("存在最高价小于最低价的数据")
    min_price = float(data["low"].min())
    max_price = float(data["high"].max())
    if max_price <= min_price:
        raise ValueError("价格范围无效")
    price_grid = np.linspace(min_price, max_price, int(bins))
    windows = tuple(sorted({int(w) for w in recent_windows if int(w) > 0}))
    max_window = max(windows, default=0)
    recent_cohorts = deque(maxlen=max_window or None)
    chip_distribution = None
    cold_start_weight = 1.0
    records = []
    for _, row in data.iterrows():
        if avg_price_col in data.columns and pd.notna(row[avg_price_col]):
            peak_price = float(row[avg_price_col])
        else:
            peak_price = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0
        today_distribution = triangular_price_weights(price_grid, row["low"], row["high"], peak_price)
        turnover = effective_turnover(row[turnover_col], is_percent=turnover_is_percent, mode=turnover_mode)
        if chip_distribution is None:
            chip_distribution = today_distribution.copy()
        else:
            old_chips = chip_distribution * (1.0 - turnover)
            new_chips = today_distribution * turnover
            chip_distribution = old_chips + new_chips
            chip_distribution /= chip_distribution.sum()
        cold_start_weight *= (1.0 - turnover)
        if max_window:
            for index in range(len(recent_cohorts)):
                recent_cohorts[index] *= (1.0 - turnover)
            recent_cohorts.append(today_distribution * turnover)
        overall_profit = profit_ratio(chip_distribution, price_grid, row["close"])
        result = {
            "date": row["date"],
            "close": float(row["close"]),
            "effective_turnover_pct": turnover * 100.0,
            "overall_profit_pct": overall_profit * 100.0,
            "estimated_avg_cost": float(np.dot(chip_distribution, price_grid) / chip_distribution.sum()),
            "cold_start_weight_pct": cold_start_weight * 100.0,
        }
        cohort_list = list(recent_cohorts)
        for window in windows:
            selected_cohorts = cohort_list[-window:]
            recent_distribution = np.sum(selected_cohorts, axis=0) if selected_cohorts else np.zeros_like(price_grid)
            recent_profit = profit_ratio(recent_distribution, price_grid, row["close"])
            profit_value = recent_profit * 100.0 if np.isfinite(recent_profit) else np.nan
            result[f"recent_{window}d_profit_pct"] = profit_value
            result[f"recent_{window}d_chip_weight_pct"] = float(recent_distribution.sum() * 100.0)
        records.append(result)
    daily = pd.DataFrame(records)
    weekly = (
        daily.assign(period=daily["date"].dt.to_period("W-FRI"))
        .groupby("period", group_keys=False).tail(1).drop(columns="period").reset_index(drop=True)
    )
    monthly = (
        daily.assign(period=daily["date"].dt.to_period("M"))
        .groupby("period", group_keys=False).tail(1).drop(columns="period").reset_index(drop=True)
    )
    return daily, weekly, monthly


def main():
    bars = pd.read_csv("daily_bars.csv")
    daily, weekly, monthly = calculate_chip_profit(
        bars, bins=2400, turnover_col="turnover_rate", turnover_is_percent=True,
        avg_price_col="avg_price", turnover_mode="linear", recent_windows=(1, 5, 20),
    )
    daily.to_csv("chip_profit_daily.csv", index=False, encoding="utf-8-sig")
    weekly.to_csv("chip_profit_weekly.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv("chip_profit_monthly.csv", index=False, encoding="utf-8-sig")
    latest = daily.iloc[-1]
    print(
        f"截至{latest['date']:%Y-%m-%d}：\n"
        f"总体获利盘：{latest['overall_profit_pct']:.2f}%\n"
        f"最近1日筹码获利盘：{latest['recent_1d_profit_pct']:.2f}%\n"
        f"最近5日筹码获利盘：{latest['recent_5d_profit_pct']:.2f}%\n"
        f"最近20日筹码获利盘：{latest['recent_20d_profit_pct']:.2f}%\n"
        f"估算平均持仓成本：{latest['estimated_avg_cost']:.3f}\n"
        f"初始化残余影响：{latest['cold_start_weight_pct']:.2f}%"
    )


if __name__ == "__main__":
    main()
