# -*- coding: utf-8 -*-
"""
筹码分布 - 获利盘比例计算脚本 (V2)
模型：三角形分布（峰值=(open+high+low+close)/4）+ 换手率衰减
turnover_rate 取值范围 0~1（3% 传 0.03）
"""
import numpy as np
import pandas as pd


def build_chip_distribution(df: pd.DataFrame, bins: int = 500):
    required = {'open', 'high', 'low', 'close', 'turnover_rate'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"缺少必要列: {missing}")
    low_all = df['low'].min()
    high_all = df['high'].max()
    price_grid = np.linspace(low_all, high_all, bins)
    chip = np.zeros(bins)
    chip_matrix = np.zeros((len(df), bins))
    win_ratio = np.zeros(len(df))
    rows = df[['open', 'high', 'low', 'close', 'turnover_rate']].to_numpy()
    for i, (o, h, l, c, turn) in enumerate(rows):
        turn = min(max(turn, 0.0), 1.0)
        A = (o + h + l + c) / 4
        A = min(max(A, l), h)
        w = np.zeros(bins)
        mask_left = (price_grid >= l) & (price_grid <= A)
        mask_right = (price_grid > A) & (price_grid <= h)
        if A > l:
            w[mask_left] = (price_grid[mask_left] - l) / (A - l)
        if h > A:
            w[mask_right] = (h - price_grid[mask_right]) / (h - A)
        if w.sum() > 0:
            w /= w.sum()
        else:
            idx = np.argmin(np.abs(price_grid - c))
            w[idx] = 1.0
        chip = chip * (1 - turn) + w * turn
        s = chip.sum()
        if s > 0:
            chip /= s
        chip_matrix[i] = chip
        win_ratio[i] = chip[price_grid <= c].sum()
    win_ratio = pd.Series(win_ratio, index=df.index, name='win_ratio')
    return win_ratio, chip_matrix, price_grid


def resample_win_ratio(daily_win_ratio: pd.Series, freq: str = 'W') -> pd.Series:
    if freq.upper() == 'D':
        return daily_win_ratio
    return daily_win_ratio.resample('ME' if freq.upper()=='M' else freq).last().dropna()


def get_win_ratio_all_freq(df: pd.DataFrame, bins: int = 500):
    daily, chip_matrix, price_grid = build_chip_distribution(df, bins=bins)
    weekly = resample_win_ratio(daily, 'W')
    monthly = resample_win_ratio(daily, 'M')
    return {
        'daily': daily,
        'weekly': weekly,
        'monthly': monthly,
        'chip_matrix': chip_matrix,
        'price_grid': price_grid,
    }
