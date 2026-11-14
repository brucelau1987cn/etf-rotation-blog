"""三路「金叉型」背离（B1/B2/B3 底背离、D1/D2/D3 顶背离）的纯函数实现。

口径来自用户提供的 双龙抄底PRO 指标源码（2026-11-15）里 MACD/KDJ/RSI 三组
简单背离段：

    A1:=BARSLAST(REF(CROSS("MACD.DIF","MACD.DEA"),1));
    B1:=REF(C,A1+1)>C AND REF("MACD.DIF",A1+1)<"MACD.DIF" AND CROSS("MACD.DIF","MACD.DEA");
    A2:=BARSLAST(REF(CROSS("KDJ.K","KDJ.D"),1));
    B2:=REF(C,A2+1)>C AND REF("KDJ.K",A2+1)<"KDJ.K" AND CROSS("KDJ.K","KDJ.D");
    A3:=BARSLAST(REF(CROSS("RSI.RSI1","RSI.RSI2"),1));
    B3:=REF(C,A3+1)>C AND REF("RSI.RSI1",A3+1)<"RSI.RSI1" AND CROSS("RSI.RSI1","RSI.RSI2");

    C1:=BARSLAST(REF(CROSS("MACD.DEA","MACD.DIF"),1));
    D1:=REF(C,C1+1)<C AND REF("MACD.DIF",C1+1)>"MACD.DIF" AND CROSS("MACD.DEA","MACD.DIF");
    C2:=BARSLAST(REF(CROSS("KDJ.D","KDJ.K"),1));
    D2:=REF(C,C2+1)<C AND REF("KDJ.K",C2+1)>"KDJ.K" AND CROSS("KDJ.D","KDJ.K");
    C3:=BARSLAST(REF(CROSS("RSI.RSI2","RSI.RSI1"),1));
    D3:=REF(C,C3+1)<C AND REF("RSI.RSI1",C3+1)>"RSI.RSI1" AND CROSS("RSI.RSI2","RSI.RSI1");

语义：`BARSLAST(REF(X,1))` 是「上一次 X 成立（不含当根）距今几根」，所以
`REF(C,A+1)` 取的是那次金叉/死叉**前一根**的收盘价。底背离 = 今天金叉，且金叉前
的收盘更高（价格创新低）、金叉前的指标更低（指标抬高）；顶背离镜像。

与 `beili_signal.py`（定量结构底背离）的区别：那套用死叉把历史切段、比较段内最低
收盘与最低 DIF，并用 INTPART/LOG 压有效数字；这套只比较「上一次金叉前一根」与
「今天」，是更宽松、更频繁的版本。两者不是包含关系，可以交叉验证。

源码里 D3 写作 `REF("RSI.RSI1",C3+1)>"RSI.RSI1"`，漏了周期参数 (6,12,24)，通达信
会按默认参数取另一个序列。这里统一按 RSI1=RSI(6)、RSI2=RSI(12) 计算，与 A3/B3 一致。

价格口径：前复权日线。KDJ 的 RSV 是区间比值、RSI 是比值，MACD 是收盘价线性组合，
同一份 bar 序列内比较不受复权基准影响。
"""
from __future__ import annotations

try:
    from jinlong_signal import ema, rsi, sma
except ModuleNotFoundError:  # pragma: no cover - 直接跑脚本时
    from scripts.jinlong_signal import ema, rsi, sma

MIN_BARS = 120
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
RSI_FAST = 6
RSI_SLOW = 12


def kdj(highs: list[float], lows: list[float], closes: list[float]):
    """通达信 KDJ(9,3,3)：RSV=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100，K=SMA(RSV,3,1)，D=SMA(K,3,1)。"""
    n = len(closes)
    rsv: list[float] = []
    for i in range(n):
        lo = max(0, i - KDJ_N + 1)
        hh = max(highs[lo:i + 1])
        ll = min(lows[lo:i + 1])
        rsv.append(50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0)
    k = sma(rsv, KDJ_M1, 1)
    d = sma(k, KDJ_M2, 1)
    return k, d


def cross_up(a: list[float | None], b: list[float | None]) -> list[bool]:
    """通达信 CROSS(A,B)：今天 A>B，且上一根 A<B（严格小于）。"""
    out: list[bool] = []
    for i in range(len(a)):
        if i == 0 or a[i] is None or b[i] is None or a[i - 1] is None or b[i - 1] is None:
            out.append(False)
            continue
        out.append(bool(a[i] > b[i] and a[i - 1] < b[i - 1]))
    return out


def barslast_prev(flags: list[bool]) -> list[int | None]:
    """BARSLAST(REF(X,1))：上一次 X 成立（不含当根）距今几根；从未成立返回 None。"""
    out: list[int | None] = []
    last = None
    for i in range(len(flags)):
        out.append(None if last is None else i - last)
        if flags[i]:
            last = i
    return out


def _ref(series: list, i: int, offset: int):
    j = i - offset
    return series[j] if 0 <= j < len(series) else None


def _divergence(closes, indicator, cross_flags, bottom: bool) -> list[bool]:
    """底/顶背离：今天交叉，且交叉前一根的收盘与指标满足背离关系。"""
    a = barslast_prev(cross_flags)
    out: list[bool] = []
    for i in range(len(closes)):
        if not cross_flags[i] or a[i] is None:
            out.append(False)
            continue
        offset = a[i] + 1
        prev_close = _ref(closes, i, offset)
        prev_ind = _ref(indicator, i, offset)
        if prev_close is None or prev_ind is None or indicator[i] is None:
            out.append(False)
            continue
        if bottom:
            out.append(bool(prev_close > closes[i] and prev_ind < indicator[i]))
        else:
            out.append(bool(prev_close < closes[i] and prev_ind > indicator[i]))
    return out


def evaluate(bars: list[dict]) -> list[dict]:
    """逐根计算三路金叉型背离，返回与 bars 等长的明细。"""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]

    ef, es = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, 9)
    k, d = kdj(highs, lows, closes)
    rsi1, rsi2 = rsi(closes, RSI_FAST), rsi(closes, RSI_SLOW)

    macd_gold = cross_up(dif, dea)
    macd_dead = cross_up(dea, dif)
    kdj_gold = cross_up(k, d)
    kdj_dead = cross_up(d, k)
    rsi_gold = cross_up(rsi1, rsi2)
    rsi_dead = cross_up(rsi2, rsi1)

    b1 = _divergence(closes, dif, macd_gold, bottom=True)
    b2 = _divergence(closes, k, kdj_gold, bottom=True)
    b3 = _divergence(closes, rsi1, rsi_gold, bottom=True)
    d1 = _divergence(closes, dif, macd_dead, bottom=False)
    d2 = _divergence(closes, k, kdj_dead, bottom=False)
    d3 = _divergence(closes, rsi1, rsi_dead, bottom=False)

    rows = []
    for i in range(len(closes)):
        rows.append({
            "trade_date": bars[i].get("trade_date") or bars[i].get("date"),
            "close": closes[i],
            "dif": dif[i],
            "dea": dea[i],
            "k": k[i],
            "d": d[i],
            "rsi6": rsi1[i],
            "rsi12": rsi2[i],
            "b1_macd": b1[i],
            "b2_kdj": b2[i],
            "b3_rsi": b3[i],
            "d1_macd": d1[i],
            "d2_kdj": d2[i],
            "d3_rsi": d3[i],
            "bottom": bool(b1[i] or b2[i] or b3[i]),
            "top": bool(d1[i] or d2[i] or d3[i]),
        })
    return rows


def _signal(rows: list[dict], hit_index: int, key: str, parts: tuple[str, ...]) -> dict:
    last_index = None
    for i in range(hit_index, -1, -1):
        if rows[i][key]:
            last_index = i
            break
    row = rows[hit_index]
    return {
        "today": bool(row[key]),
        "today_parts": [p for p in parts if row[p]],
        "last_hit_date": rows[last_index]["trade_date"] if last_index is not None else None,
        "bars_since_hit": (hit_index - last_index) if last_index is not None else None,
        "close": row["close"],
        "dif": row["dif"],
        "k": row["k"],
        "rsi6": row["rsi6"],
    }


def signal_at(bars: list[dict], as_of: str | None = None) -> dict:
    """取指定交易日的三路金叉型背离状态（底背离 + 顶背离）。"""
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

    bottom = _signal(rows, hit_index, "bottom", ("b1_macd", "b2_kdj", "b3_rsi"))
    top = _signal(rows, hit_index, "top", ("d1_macd", "d2_kdj", "d3_rsi"))
    return {
        "status": "ok",
        "as_of": str(rows[hit_index]["trade_date"])[:10],
        "bottom": bottom,
        "top": top,
        "history_bars": len(bars),
        "model": "shuanglong-cross-divergence-v1",
    }
