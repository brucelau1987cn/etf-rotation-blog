#!/usr/bin/env python3
"""交叉验证：a-stock-data 的 CYQ 三角分布算法 vs 同花顺官方筹码数据（chip_list）。

标答 = 同花顺 chip_list 的 average_cost + 复算 closing_profit（sum(jeton<=close)/sum(jeton)）。
对照 = scripts/reference/a-stock-data/chip_distribution.py 的本地推演。
"""
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "reference" / "a-stock-data"))
from chip_distribution import chip_distribution, fetch_ohlc_turnover  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0"}


def ths_chip(code: str, market: int, date: str) -> dict:
    """取同花顺 chip_list 单标的筹码曲线，复算获利比例 + 平均成本。"""
    day = datetime.strptime(date, "%Y-%m-%d")
    start = int(day.replace(hour=9, minute=30).timestamp() * 1000)
    end = int(day.replace(hour=15, minute=0).timestamp() * 1000)
    url = (f"https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection/stock/v1/chip_list"
           f"?chip_type=all&stock_code={code}&stock_market={market}"
           f"&start_date={start}&end_date={end}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        d = json.load(r)
    day_data = (d.get("data") or {}).get("list", {}).get(date.replace("-", ""))
    if not day_data:
        raise RuntimeError(f"chip_list 无 {date} 数据")
    summary = day_data["summary"]
    close = float(summary["close_price"])
    avg_cost = float(summary["average_cost"])
    curve = day_data["curve_data"]["list"]
    total = sum(float(x["jeton"]) for x in curve)
    profit = sum(float(x["jeton"]) for x in curve if float(x["price"]) <= close) / total
    return {"close": close, "average_cost": avg_cost, "profit_ratio": profit}


def main():
    code, market, date = "600519", 17, "2026-08-21"
    ths = ths_chip(code, market, date)
    print(f"=== 同花顺官方（{date}，茅台）===")
    print(f"  收盘 {ths['close']} | 平均成本 {ths['average_cost']:.2f} | 获利比例 {ths['profit_ratio']*100:.2f}%")

    print("\n=== CYQ 三角分布推演（不同窗口）===")
    for start in ("2026-02-01", "2025-08-21", "2025-01-01"):
        df = fetch_ohlc_turnover(code, start, date)
        r = chip_distribution(df, decay=1.0)
        dc = abs(r["avg_cost"] - ths["average_cost"]) / ths["average_cost"] * 100
        dp = abs(r["profit_ratio"] - ths["profit_ratio"]) * 100
        print(f"  窗口 {start}~{date}（{len(df)}日）: 平均成本 {r['avg_cost']:.2f} "
              f"(差{dc:.2f}%) | 获利比例 {r['profit_ratio']*100:.2f}% (差{dp:.1f}pp) | 筹码峰 {r['peak_price']:.2f}")


if __name__ == "__main__":
    main()
