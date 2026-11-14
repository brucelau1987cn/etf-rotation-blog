"""底背离（直接 + 隔峰）—— 双龙抄底PRO「定量结构」段的纯函数实现。

口径来自用户提供的 双龙抄底PRO 指标源码（2026-11-15）里「底部结构预警」一段：

    死叉:=CROSS(DEA,DIF);
    N1:=BARSLAST(死叉);              {最近一次死叉距今几根}
    N2:=REF(BARSLAST(死叉),N1+1);
    CL1:=LLV(C,N1+1);  DIFL1:=LLV(DIF,N1+1);   {最近一次死叉后的最低收盘 / 最低 DIF}
    CL2:=REF(CL1,N1+1); DIFL2:=REF(DIFL1,N1+1);{再往前一段的最低}
    CL3:=REF(CL2,N1+1); DIFL3:=REF(DIFL2,N1+1);{再往前一段的最低}
    PDIFL2:=IF(DIFL2>0,INTPART(LOG(DIFL2))-1,INTPART(LOG(-DIFL2))-1);
    MDIFL2:=INTPART(DIFL2/POW(10,PDIFL2));
    MDIFB2:=INTPART(DIF/POW(10,PDIFL2));
    （PDIFL3 / MDIFL3 / MDIFB3 同理）
    直接底背离:=(CL1<CL2) AND (MDIFB2>MDIFL2) AND (MACD<0 AND REF(MACD,1)<0) AND MDIFB2<=REF(MDIFB2,1);
    隔峰底背离:=(CL1<CL3 AND CL3<CL2) AND (MDIFB3>MDIFL3) AND (MACD<0 AND REF(MACD,1)<0) AND MDIFB3<=REF(MDIFB3,1);
    B6: 直接底背离 OR 隔峰底背离;
    底背离消失:=(REF(直接底背离,1) AND DIFL1<=DIFL2) OR (REF(隔峰底背离,1) AND DIFL1<=DIFL3);

即「价格创新低而 DIF 抬高」（直接底背离看相邻两段，隔峰底背离看跨一段的三段），
且要求 MACD 柱仍在零轴下方；`MDIF*` 那套 INTPART/LOG 是把 DIF 按量级压成 2 位有效
数字再比较，避免 DIF 绝对值随价格水平缩放后失真。

N2/N3 在源码里只喂给 S5/ST（强度柱），不参与背离判定，这里不算。

价格口径：前复权日线。DIF/DEA 是收盘价的线性组合，MACD 柱 2*(DIF-DEA) 同理，
所以同一份 bar 序列内的比较不受复权基准影响，但跨基准拼接会失真，契约记 adjustment。
"""
from __future__ import annotations

import math

try:
    from jinlong_signal import ema
except ModuleNotFoundError:  # pragma: no cover - 直接跑脚本时
    from scripts.jinlong_signal import ema

MIN_BARS = 120          # EMA26+EMA9 收敛 + 三段死叉结构所需
SCALE = 10


def dif_dea(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, signal)
    return dif, dea


def cross_up(values_a: list[float], values_b: list[float]) -> list[bool]:
    """通达信 CROSS(A,B)：今天 A>B，且上一根 A<B（严格小于）。"""
    out: list[bool] = []
    for i in range(len(values_a)):
        if i == 0:
            out.append(False)
            continue
        out.append(
            values_a[i] > values_b[i] and values_a[i - 1] < values_b[i - 1]
        )
    return out


def _intpart(value: float) -> float:
    """通达信 INTPART：向零取整（负数也向零，不是向下取整）。"""
    return float(math.trunc(value))


def _scale_prefix(value: float | None) -> float | None:
    """PDIFL*：把 |DIF| 压到 2 位有效数字所需要的 10 的幂次。"""
    if value is None or value == 0:
        return None
    return _intpart(math.log10(abs(value))) - 1


def _scaled(value: float | None, prefix: float | None) -> float | None:
    if value is None or prefix is None:
        return None
    return _intpart(value / (10.0 ** prefix))


def evaluate(bars: list[dict]) -> list[dict]:
    """逐根计算底背离各中间量，返回与 bars 等长的明细。"""
    closes = [float(b["close"]) for b in bars]
    dif, dea = dif_dea(closes)
    hist = [2 * (d - s) for d, s in zip(dif, dea)]
    death = cross_up(dea, dif)          # CROSS(DEA,DIF)

    n = len(closes)
    n1: list[int | None] = []
    last_death = None
    for i in range(n):
        if death[i]:
            last_death = i
        n1.append(None if last_death is None else i - last_death)

    cl1: list[float | None] = [None] * n
    difl1: list[float | None] = [None] * n
    for i in range(n):
        if n1[i] is None:
            continue
        lo = i - n1[i]
        cl1[i] = min(closes[lo:i + 1])
        difl1[i] = min(dif[lo:i + 1])

    def _ref(series, i, offset):
        j = i - offset
        return series[j] if 0 <= j < n else None

    cl2: list[float | None] = [None] * n
    difl2: list[float | None] = [None] * n
    cl3: list[float | None] = [None] * n
    difl3: list[float | None] = [None] * n
    for i in range(n):
        if n1[i] is None:
            continue
        offset = n1[i] + 1
        cl2[i] = _ref(cl1, i, offset)
        difl2[i] = _ref(difl1, i, offset)
        cl3[i] = _ref(cl2, i, offset)
        difl3[i] = _ref(difl2, i, offset)

    p2 = [_scale_prefix(v) for v in difl2]
    p3 = [_scale_prefix(v) for v in difl3]
    mdifl2 = [_scaled(difl2[i], p2[i]) for i in range(n)]
    mdifl3 = [_scaled(difl3[i], p3[i]) for i in range(n)]
    mdifb2 = [_scaled(dif[i], p2[i]) for i in range(n)]
    mdifb3 = [_scaled(dif[i], p3[i]) for i in range(n)]

    direct: list[bool] = []
    separated: list[bool] = []
    for i in range(n):
        macd_below = hist[i] < 0 and i > 0 and hist[i - 1] < 0
        prev_b2 = mdifb2[i - 1] if i > 0 else None
        prev_b3 = mdifb3[i - 1] if i > 0 else None
        c1, c2, c3 = cl1[i], cl2[i], cl3[i]
        b2, l2 = mdifb2[i], mdifl2[i]
        b3, l3 = mdifb3[i], mdifl3[i]
        direct.append(bool(
            None not in (c1, c2, b2, l2, prev_b2)
            and c1 < c2 and b2 > l2 and macd_below and b2 <= prev_b2  # type: ignore[operator]
        ))
        separated.append(bool(
            None not in (c1, c2, c3, b3, l3, prev_b3)
            and c1 < c3 and c3 < c2 and b3 > l3 and macd_below and b3 <= prev_b3  # type: ignore[operator]
        ))

    vanish: list[bool] = []
    for i in range(n):
        v = False
        f1, f2, f3 = difl1[i], difl2[i], difl3[i]
        if i > 0 and f1 is not None:
            if direct[i - 1] and f2 is not None:
                v = f1 <= f2
            elif separated[i - 1] and f3 is not None:
                v = f1 <= f3
        vanish.append(bool(v))

    rows = []
    for i in range(n):
        rows.append({
            "trade_date": bars[i].get("trade_date") or bars[i].get("date"),
            "close": closes[i],
            "dif": dif[i],
            "dea": dea[i],
            "macd_hist": hist[i],
            "bars_since_death": n1[i],
            "cl1": cl1[i],
            "cl2": cl2[i],
            "cl3": cl3[i],
            "difl1": difl1[i],
            "difl2": difl2[i],
            "difl3": difl3[i],
            "direct_divergence": direct[i],
            "separated_divergence": separated[i],
            "divergence": bool(direct[i] or separated[i]),
            "divergence_void": vanish[i],
        })
    return rows


def signal_at(bars: list[dict], as_of: str | None = None) -> dict:
    """取指定交易日的底背离状态。

    「有效底背离」= 最近一次成立之后没有再出现「底背离消失」。返回值同时给出
    当日是否成立、最近一次成立日期、距今根数、以及是否已被消失条件否定。
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
        if rows[i]["divergence"]:
            last_index = i
            break
    voided = False
    if last_index is not None:
        voided = any(rows[j]["divergence_void"] for j in range(last_index + 1, hit_index + 1))

    row = rows[hit_index]
    return {
        "status": "ok",
        "as_of": str(row["trade_date"])[:10],
        "today": bool(row["divergence"]),
        "direct_today": bool(row["direct_divergence"]),
        "separated_today": bool(row["separated_divergence"]),
        "last_hit_date": rows[last_index]["trade_date"] if last_index is not None else None,
        "bars_since_hit": (hit_index - last_index) if last_index is not None else None,
        "voided": voided,
        "close": row["close"],
        "dif": row["dif"],
        "macd_hist": row["macd_hist"],
        "cl1": row["cl1"],
        "cl2": row["cl2"],
        "cl3": row["cl3"],
        "difl1": row["difl1"],
        "difl2": row["difl2"],
        "difl3": row["difl3"],
        "history_bars": len(bars),
        "model": "shuanglong-dingliang-jiegou-divergence-v1",
    }
