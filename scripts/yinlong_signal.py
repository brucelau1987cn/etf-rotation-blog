"""银龙（双龙抄底PRO 的另一支）选股信号的纯函数实现。

口径来自《银龙选股指标说明报告》（2026-11-14）所引用的 YinLong.txt：

    C81:=REF(C,1);
    RN:=9;
    RSI1:=SMA(MAX(C-C81,0),RN,1)/SMA(ABS(C-C81),RN,1)*100;
    DIF:=EMA(CLOSE,12)-EMA(CLOSE,26);
    DEA:=EMA(DIF,9);
    MACD:=(DIF-DEA)*2;
    XG: CROSS(RSI1,20) AND MACD<0;

即：9 周期 RSI 向上穿过 20，且当天 MACD 柱线为负。报告已核对源码无冗余逻辑、
无额外 FILTER 去重，因此这里直接按两个条件实现。

与金龙的区别（同报告第二节）：银龙没有均线偏离门槛、没有振幅与 VAR2 低位约束、
不看历史 13 根内是否有门槛信号，RSI 用 9 周期并要求**上穿** 20（金龙是 7 周期、
昨天低于 20、今天回升即可，且经多层 FILTER）。两者不是包含关系。

价格口径：前复权日线。RSI 是比值、对复权因子不敏感；但 `MACD<0` 虽然只看符号，
仍要求所有 bar 使用同一复权基准，否则跨除权日的 DIF/DEA 会失真。
"""
from __future__ import annotations

try:
    from jinlong_signal import ema, macd_hist, rsi
except ModuleNotFoundError:  # pragma: no cover - 直接跑脚本时
    from scripts.jinlong_signal import ema, macd_hist, rsi

RSI_PERIOD = 9
RSI_LEVEL = 20.0
SMA_WEIGHT = 1
MIN_BARS = 60          # EMA26 + EMA9 收敛 + RSI9 递推足够稳定


def cross_up(values: list[float | None], level: float) -> list[bool]:
    """通达信 CROSS(A,B)：今天 A>B，且上一根 A<B（严格小于）。

    昨天的值恰好等于 level 时不算上穿（那属于「已在上方」而非「从下方向上穿」），
    与报告里「23 → 30 没有本次上穿」的判断一致。
    """
    out: list[bool] = []
    for i, value in enumerate(values):
        previous = values[i - 1] if i else None
        out.append(bool(
            value is not None and previous is not None and value > level and previous < level
        ))
    return out


def evaluate(bars: list[dict]) -> list[dict]:
    """逐根计算银龙各中间量，返回与 bars 等长的明细。"""
    closes = [float(b["close"]) for b in bars]
    rsi9 = rsi(closes, RSI_PERIOD)
    hist = macd_hist(closes)
    crossed = cross_up(rsi9, RSI_LEVEL)
    rows = []
    for i in range(len(closes)):
        rows.append({
            "trade_date": bars[i].get("trade_date") or bars[i].get("date"),
            "close": closes[i],
            "rsi9": rsi9[i],
            "macd_hist": hist[i],
            "cross_up_20": crossed[i],
            "xg": bool(crossed[i] and hist[i] < 0),
        })
    return rows


def signal_at(bars: list[dict], as_of: str | None = None) -> dict:
    """取指定交易日的银龙状态。

    bars 不足 MIN_BARS 或指定日期不在数据里时返回 unavailable，不静默当成没信号。
    """
    if len(bars) < MIN_BARS:
        return {"status": "unavailable", "reason": f"insufficient_bars:{len(bars)}/{MIN_BARS}"}
    rows = evaluate(bars)
    target = str(as_of or rows[-1]["trade_date"])[:10]
    hit_index = None
    for i, row in enumerate(rows):
        if str(row["trade_date"])[:10] == target:
            hit_index = i
    if hit_index is None:
        return {"status": "unavailable", "reason": f"no_bar_on:{target}"}
    last_index = None
    for i in range(hit_index, -1, -1):
        if rows[i]["xg"]:
            last_index = i
            break
    row = rows[hit_index]
    previous = rows[hit_index - 1] if hit_index else None
    return {
        "status": "ok",
        "as_of": str(row["trade_date"])[:10],
        "xg": bool(row["xg"]),
        "last_hit_date": rows[last_index]["trade_date"] if last_index is not None else None,
        "bars_since_hit": (hit_index - last_index) if last_index is not None else None,
        "close": row["close"],
        "rsi9": row["rsi9"],
        "rsi9_previous": previous["rsi9"] if previous else None,
        "macd_hist": row["macd_hist"],
        "history_bars": len(bars),
        "model": "yinlong-shuanglong-chaodi-pro-v1",
    }
