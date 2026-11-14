"""金龙（双龙抄底PRO）选股信号的纯函数实现。

口径来自《金龙选股指标说明报告》（2026-11-14）所引用的 JinLong.txt 片段：

    启动1: C/MA(C,40) < 0.74 AND (H-L)/REF(C,1) > 0.05 AND COUNT(振幅>5%,5) >= 2
    VAR2 := ((MA(C,30)-LOW)/MA(C,60))*200
    原始回升: REF(RSI(7),1) < 20 AND RSI(7) > REF(RSI(7),1)
    GL1  : FILTER(原始回升, 5)
    波段1: FILTER(REF(启动1,1) AND C>REF(C,1) AND VAR2>20 AND GL1, 10)
    选股1: FILTER(REF(启动1,1) AND C>REF(C,1) AND MACD.MACD(12,26,9)>-1.5, 10)
    XG   : COUNT(选股1,13) >= 1 AND 波段1

报告已证明原始代码里的「确定1 = 波段1 AND CROSS(NTDF1,-0.05)」对最终输出没有贡献
（`波段1 OR 确定1` 恒等于 `波段1`，且 `确定1 OR C>REF(C,1)` 恒等于 `C>REF(C,1)`），
因此这里只实现简化后的等价形式，不重算动能段。

价格口径：全部按**前复权**日线计算。启动1、原始回升、VAR2 都是比值，复权因子整体缩放
不影响结果；但 `MACD.MACD > -1.5` 是绝对值门槛，会随复权基准变化（报告已标注该边界），
所以契约里必须记录 adjustment。
"""
from __future__ import annotations

RATIO_DEEP_DROP = 0.74      # C/MA(C,40) < 0.74
AMPLITUDE_PCT = 0.05        # (H-L)/REF(C,1) > 5%
AMPLITUDE_WINDOW = 5        # 5 根内至少 2 次
AMPLITUDE_MIN_COUNT = 2
RSI_PERIOD = 7
RSI_FLOOR = 20.0
GL_FILTER = 5
BAND_FILTER = 10
VAR2_THRESHOLD = 20.0
MACD_THRESHOLD = -1.5
HISTORY_WINDOW = 13
MIN_BARS = 120              # MA60 + EMA26/9 收敛所需


def ma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float]:
    """通达信 EMA(X,N) = (2*X + (N-1)*EMA_prev)/(N+1)，用首值播种。"""
    out: list[float] = []
    prev = None
    for v in values:
        prev = v if prev is None else (2 * v + (n - 1) * prev) / (n + 1)
        out.append(prev)
    return out


def sma(values: list[float], n: int, m: int = 1) -> list[float]:
    """通达信 SMA(X,N,M) = (M*X + (N-M)*SMA_prev)/N，用首值播种。"""
    out: list[float] = []
    prev = None
    for v in values:
        prev = v if prev is None else (m * v + (n - m) * prev) / n
        out.append(prev)
    return out


def rsi(closes: list[float], n: int = RSI_PERIOD) -> list[float | None]:
    """通达信 RSI: SMA(MAX(C-LC,0),N,1)/SMA(ABS(C-LC),N,1)*100。"""
    gain, change = [], []
    for i, c in enumerate(closes):
        diff = 0.0 if i == 0 else c - closes[i - 1]
        gain.append(max(diff, 0.0))
        change.append(abs(diff))
    up = sma(gain, n, 1)
    down = sma(change, n, 1)
    out: list[float | None] = []
    for i in range(len(closes)):
        out.append(None if i == 0 or down[i] == 0 else up[i] / down[i] * 100.0)
    return out


def macd_hist(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float]:
    """通达信 MACD.MACD = 2*(EMA(C,12)-EMA(C,26)-EMA9(EMA12-EMA26))。"""
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, signal)
    return [2 * (d - s) for d, s in zip(dif, dea)]


def filter_signal(flags: list[bool], n: int) -> list[bool]:
    """通达信 FILTER(X,N)：触发一次后，后续 N 根内同信号被屏蔽。"""
    out: list[bool] = []
    suppressed_until = -1
    for i, flag in enumerate(flags):
        if flag and i > suppressed_until:
            out.append(True)
            suppressed_until = i + n
        else:
            out.append(False)
    return out


def count_true(flags: list[bool], n: int) -> list[int]:
    """通达信 COUNT(X,N)：含当前根在内的最近 N 根中成立次数。"""
    out: list[int] = []
    for i in range(len(flags)):
        lo = max(0, i - n + 1)
        out.append(sum(1 for f in flags[lo:i + 1] if f))
    return out


def evaluate(bars: list[dict]) -> list[dict]:
    """逐根计算金龙各中间量，返回与 bars 等长的明细。"""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    ma40, ma30, ma60 = ma(closes, 40), ma(closes, 30), ma(closes, 60)
    rsi7, hist = rsi(closes), macd_hist(closes)

    amplitude = [None]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        amplitude.append((highs[i] - lows[i]) / prev if prev else None)
    amp_flags = [a is not None and a > AMPLITUDE_PCT for a in amplitude]
    amp_count = count_true(amp_flags, AMPLITUDE_WINDOW)

    start1: list[bool] = []
    for i in range(len(closes)):
        ok = (
            ma40[i] is not None
            and ma40[i] > 0
            and closes[i] / ma40[i] < RATIO_DEEP_DROP
            and amp_flags[i]
            and amp_count[i] >= AMPLITUDE_MIN_COUNT
        )
        start1.append(ok)

    rebound_raw = [
        i > 0 and rsi7[i] is not None and rsi7[i - 1] is not None
        and rsi7[i - 1] < RSI_FLOOR and rsi7[i] > rsi7[i - 1]
        for i in range(len(closes))
    ]
    gl1 = filter_signal(rebound_raw, GL_FILTER)

    prev_start = [False] + start1[:-1]
    rising = [i > 0 and closes[i] > closes[i - 1] for i in range(len(closes))]
    var2 = [
        None if (ma60[i] is None or ma60[i] == 0) else ((ma30[i] - lows[i]) / ma60[i]) * 200
        for i in range(len(closes))
    ]

    band_raw = [
        prev_start[i] and rising[i] and var2[i] is not None and var2[i] > VAR2_THRESHOLD and gl1[i]
        for i in range(len(closes))
    ]
    band1 = filter_signal(band_raw, BAND_FILTER)

    pick_raw = [
        prev_start[i] and rising[i] and hist[i] > MACD_THRESHOLD for i in range(len(closes))
    ]
    pick1 = filter_signal(pick_raw, BAND_FILTER)
    pick_count = count_true(pick1, HISTORY_WINDOW)

    rows = []
    for i in range(len(closes)):
        rows.append({
            "trade_date": bars[i].get("trade_date") or bars[i].get("date"),
            "close": closes[i],
            "start1": start1[i],
            "gl1": gl1[i],
            "band1": band1[i],
            "pick1": pick1[i],
            "xg": bool(pick_count[i] >= 1 and band1[i]),
            "rsi7": rsi7[i],
            "var2": var2[i],
            "macd_hist": hist[i],
            "amplitude": amplitude[i],
        })
    return rows


def signal_at(bars: list[dict], as_of: str | None = None) -> dict:
    """取指定交易日的金龙状态。

    返回 status=ok 时带 xg（当日是否入选）、最近一次 xg 日期与间隔；bars 不足
    MIN_BARS 或指定日期不在数据里时返回 unavailable，不静默当成「没信号」。
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
    return {
        "status": "ok",
        "as_of": str(row["trade_date"])[:10],
        "xg": bool(row["xg"]),
        "last_hit_date": rows[last_index]["trade_date"] if last_index is not None else None,
        "bars_since_hit": (hit_index - last_index) if last_index is not None else None,
        "close": row["close"],
        "rsi7": row["rsi7"],
        "var2": row["var2"],
        "macd_hist": row["macd_hist"],
        "start1_yesterday": bool(rows[hit_index - 1]["start1"]) if hit_index else False,
        "band1": bool(row["band1"]),
        "pick1_window": sum(
            1 for r in rows[max(0, hit_index - HISTORY_WINDOW + 1):hit_index + 1] if r["pick1"]
        ),
        "history_bars": len(bars),
        "model": "jinlong-shuanglong-chaodi-pro-v1",
    }
