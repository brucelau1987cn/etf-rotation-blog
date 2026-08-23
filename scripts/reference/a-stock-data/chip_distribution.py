#!/usr/bin/env python3
"""筹码分布 CYQ — 本地三角分布推演（获利比例 / 平均成本 / 成本区间 / 筹码峰）。

来源：https://github.com/simonlin1212/a-stock-data（SKILL.md §4.6，V3.7.0）
License：Apache 2.0（原项目），抽取整合为自包含脚本，供参考/对比，未接入生产。

算法要点（业界通行 CYQ 本地推演口径）：
  1. 东财没有公开 CYQ 接口（push2/push2his 实测 404），故本地推演。
  2. 历史筹码按换手率衰减，当日成交量按三角分布撒进 [low, high]（峰值在均价）。
  3. 初始筹码必须播种为「首日全部流通盘」，不能从零起步（否则会把窗口前存量持仓勾销）。
  4. 输入 OHLC 必须用前复权价（筹码成本跨除权日才有意义）。
  5. 停牌日（tradestatus != 1）不参与换手衰减。

用法：
  python3 chip_distribution.py 600519 [--start 2026-02-01] [--end 2026-08-18] [--decay 1.0]

依赖：numpy pandas baostock（取 OHLC + 换手率用，纯算法部分只需 numpy/pandas）。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────────
# 核心算法（纯 numpy/pandas，不依赖数据源）
# ──────────────────────────────────────────────────────────────────────────

def _triangular_weights(grid: np.ndarray, low: float, high: float, avg: float) -> np.ndarray:
    """当日筹码在价格网格上的三角分布权重（峰值在均价，面积归一）。"""
    w = np.zeros_like(grid)
    if not np.isfinite([low, high, avg]).all() or high < low:
        return w
    if high - low < 1e-9:                       # 一字板：全部堆在一个价位
        w[np.argmin(np.abs(grid - low))] = 1.0
        return w
    avg = min(max(avg, low), high)              # 均价必须落在当日区间内
    left = (grid >= low) & (grid <= avg)
    right = (grid > avg) & (grid <= high)
    if avg - low > 1e-9:
        w[left] = (grid[left] - low) / (avg - low)
    else:
        w[left] = 1.0
    if high - avg > 1e-9:
        w[right] = (high - grid[right]) / (high - avg)
    else:
        w[right] = 1.0
    total = w.sum()
    if total > 0:
        return w / total
    # 兜底：当日振幅窄于网格步长时可能一个网格点都没落进 [low, high]，
    # 权重全为 0。低波动标的（银行股等）+ 长窗口会累积成很大偏差，映射到最近网格点。
    w[np.argmin(np.abs(grid - avg))] = 1.0
    return w


def chip_distribution(df: pd.DataFrame, grid_size: int = 300, decay: float = 1.0) -> dict:
    """筹码分布 — df 需含 date/high/low/close/turn（turn 为百分数，0.31 表示 0.31%）。

    decay: 换手衰减系数。1.0=按真实换手率换手；同花顺口径常用 1.5~2.0 加快历史筹码消散。
    """
    need = {"date", "high", "low", "close", "turn"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"chip_distribution 缺少列: {sorted(missing)}（date 用于强制时间升序）")
    d = df.dropna(subset=["high", "low", "close", "turn"]).copy()
    d = d[d["high"] > 0]
    if d.empty:
        raise ValueError("chip_distribution: 有效行数为 0（检查是否全是停牌日，或字段类型不对）")
    d = d.sort_values("date").reset_index(drop=True)

    lo, hi = float(d["low"].min()), float(d["high"].max())
    pad = (hi - lo) * 0.02 or max(lo * 0.02, 0.01)
    grid = np.linspace(lo - pad, hi + pad, grid_size)

    chips = None
    for row in d.itertuples(index=False):
        t = float(row.turn) / 100.0 * decay
        t = min(max(t, 0.0), 1.0)               # 换手率兜到 [0,1]，防异常值把筹码一次清零
        avg = (float(row.high) + float(row.low) + float(row.close)) / 3.0
        w = _triangular_weights(grid, float(row.low), float(row.high), avg)
        if w.sum() <= 0:
            continue
        if chips is None:
            chips = w.copy()                    # 首日分布 = 期初全部流通筹码
            continue
        chips = chips * (1.0 - t) + w * t
    if chips is None:
        raise RuntimeError("chip_distribution: 所有交易日的价格区间都无效，无法构建分布")

    total = chips.sum()
    if total <= 0:
        raise RuntimeError("chip_distribution: 筹码总量为 0，无法计算指标")
    chips = chips / total

    price = float(d["close"].iloc[-1])
    cum = np.cumsum(chips)

    def price_at(q: float) -> float:
        return float(np.interp(q, cum, grid))

    p05, p15, p85, p95 = (price_at(q) for q in (0.05, 0.15, 0.85, 0.95))
    peak_i = int(np.argmax(chips))
    return {
        "price": price,
        "profit_ratio": float(chips[grid <= price].sum()),      # 获利比例
        "avg_cost": float((grid * chips).sum()),                # 平均成本
        "cost_90": (p05, p95),
        "cost_70": (p15, p85),
        "concentration_90": float((p95 - p05) / (p95 + p05)) if p95 + p05 else None,
        "concentration_70": float((p85 - p15) / (p85 + p15)) if p85 + p15 else None,
        "peak_price": float(grid[peak_i]),                      # 筹码峰
        "histogram": [(float(pp), float(cc)) for pp, cc in zip(grid, chips) if cc > 1e-6],
    }


# ──────────────────────────────────────────────────────────────────────────
# baostock 取数（OHLC + 换手率；筹码算法只需 numpy/pandas，本段仅作数据源示例）
# ──────────────────────────────────────────────────────────────────────────

@contextmanager
def bs_session():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
    try:
        yield
    finally:
        bs.logout()


def _rs_to_df(rs) -> pd.DataFrame:
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def _bs_code(code: str) -> str:
    """6位代码 → baostock 格式；北交所在登录前就拦掉。"""
    code = str(code).zfill(6)
    if code[:2] in ("60", "68", "90"):
        return f"sh.{code}"
    if code[:2] in ("00", "30", "20"):
        return f"sz.{code}"
    raise ValueError(f"baostock 不支持该代码: {code}（北交所 4/8/92/920 号段会被服务端拒绝）")


def fetch_ohlc_turnover(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """用 baostock 取前复权 OHLC + 换手率（停牌日已过滤）。"""
    import baostock as bs
    bs_code = _bs_code(code)
    fields = "date,open,high,low,close,turn,tradestatus"
    with bs_session():
        rs = bs.query_history_k_data_plus(
            bs_code, fields, start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="2")        # 2=前复权，筹码成本必须用复权价
        k = _rs_to_df(rs)
    for c in ("open", "high", "low", "close", "turn"):
        k[c] = pd.to_numeric(k[c], errors="coerce")
    k = k[k["tradestatus"] == "1"]                # 停牌日不参与换手衰减
    return k


def main() -> int:
    parser = argparse.ArgumentParser(description="筹码分布 CYQ 本地推演（a-stock-data 参考实现）")
    parser.add_argument("code", help="6 位 A 股代码，如 600519")
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--end", default="2026-08-18")
    parser.add_argument("--decay", type=float, default=1.0,
                        help="换手衰减系数，1.0=真实换手率，同花顺口径常用 1.5~2.0")
    args = parser.parse_args()

    k = fetch_ohlc_turnover(args.code, args.start, args.end)
    r = chip_distribution(k, decay=args.decay)
    print(f"{args.code} | 窗口 {k['date'].iloc[0]} ~ {k['date'].iloc[-1]}（{len(k)} 个交易日）")
    print(f"现价 {r['price']:.2f} | 获利比例 {r['profit_ratio']*100:.2f}% | 平均成本 {r['avg_cost']:.2f}")
    print(f"90%成本区间 {r['cost_90'][0]:.2f}~{r['cost_90'][1]:.2f} 集中度 {r['concentration_90']*100:.2f}%")
    print(f"70%成本区间 {r['cost_70'][0]:.2f}~{r['cost_70'][1]:.2f} 集中度 {r['concentration_70']*100:.2f}%")
    print(f"筹码峰 {r['peak_price']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
