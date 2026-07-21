#!/usr/bin/env python3
"""Deterministic analytics helpers for the public futures compass snapshot."""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Iterable


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def mean(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def moving_average(rows: list[dict[str, Any]], field: str, window: int) -> float | None:
    if len(rows) < window:
        return None
    return mean(finite(row.get(field)) for row in rows[-window:])


def atr14(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 15:
        return None
    ranges: list[float] = []
    for index in range(len(rows) - 14, len(rows)):
        current = rows[index]
        previous = finite(rows[index - 1].get("close"))
        high = finite(current.get("high"))
        low = finite(current.get("low"))
        if high is None or low is None or previous is None:
            return None
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    return mean(ranges)


def fvg_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for index in range(len(rows) - 1, 1, -1):
        first = rows[index - 2]
        third = rows[index]
        first_high = finite(first.get("high"))
        first_low = finite(first.get("low"))
        third_high = finite(third.get("high"))
        third_low = finite(third.get("low"))
        if None in {first_high, first_low, third_high, third_low}:
            continue
        later = rows[index + 1 :]
        if third_low > first_high:
            filled = any((finite(row.get("low")) or math.inf) <= first_high for row in later)
            return {"direction": "向上FVG", "lower": first_high, "upper": third_low, "status": "已回补" if filled else "未回补"}
        if third_high < first_low:
            filled = any((finite(row.get("high")) or -math.inf) >= first_low for row in later)
            return {"direction": "向下FVG", "lower": third_high, "upper": first_low, "status": "已回补" if filled else "未回补"}
    return {"direction": "无明确FVG", "lower": None, "upper": None, "status": "未知"}


def structure_state(rows: list[dict[str, Any]], current_price: float | None) -> dict[str, Any]:
    if current_price is None or len(rows) < 6:
        return {"structure": "未知", "bos_level": None, "choch_level": None}
    previous = rows[-6:-1]
    highs = [finite(row.get("high")) for row in previous]
    lows = [finite(row.get("low")) for row in previous]
    if any(value is None for value in highs + lows):
        return {"structure": "未知", "bos_level": None, "choch_level": None}
    upper = max(highs)  # type: ignore[arg-type]
    lower = min(lows)  # type: ignore[arg-type]
    if current_price > upper:
        return {"structure": "向上BOS", "bos_level": upper, "choch_level": lower}
    if current_price < lower:
        return {"structure": "向下BOS", "bos_level": lower, "choch_level": upper}
    closes = [finite(row.get("close")) for row in rows[-4:]]
    if all(value is not None for value in closes):
        if closes[-1] > closes[-2] > closes[-3]:
            return {"structure": "多头CHoCH观察", "bos_level": upper, "choch_level": lower}
        if closes[-1] < closes[-2] < closes[-3]:
            return {"structure": "空头CHoCh观察", "bos_level": lower, "choch_level": upper}
    return {"structure": "区间震荡", "bos_level": upper, "choch_level": lower}


def trend_label(price: float | None, ma5: float | None, ma10: float | None, ma20: float | None) -> str:
    if None in {price, ma5, ma10, ma20}:
        return "未知"
    if price > ma5 > ma10 > ma20:
        return "多头排列"
    if price < ma5 < ma10 < ma20:
        return "空头排列"
    if price >= ma20:
        return "MA20上方震荡"
    return "MA20下方震荡"


def action_label(capital_state: str, structure: str, trend: str) -> str:
    if structure == "向上BOS" and capital_state == "增仓上涨":
        return "多头确认"
    if structure == "向下BOS" and capital_state == "增仓下跌":
        return "空头确认"
    if capital_state == "增仓上涨":
        return "偏多观察"
    if capital_state == "增仓下跌":
        return "偏空观察"
    if capital_state == "减仓上涨":
        return "空头回补"
    if capital_state == "减仓下跌":
        return "多头离场"
    return trend


def tick_round(value: float | None, tick: float | None) -> float | None:
    if value is None or tick is None or tick <= 0:
        return value
    return round(round(value / tick) * tick, 8)


def rows_for_code(db: sqlite3.Connection, code: str, limit: int = 60) -> list[dict[str, Any]]:
    records = db.execute(
        "SELECT trade_date,open,high,low,close,volume,open_interest,settle "
        "FROM daily_bars WHERE code=? ORDER BY trade_date DESC LIMIT ?",
        (code, limit),
    ).fetchall()
    return [dict(row) for row in reversed(records)]


def latest_receipt(db: sqlite3.Connection, code: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT trade_date,receipt,change_value,source,fetched_at FROM warehouse_receipts "
        "WHERE code=? ORDER BY trade_date DESC LIMIT 1",
        (code,),
    ).fetchone()
    if not row:
        return {"status": "unknown", "trade_date": None, "receipt": None, "change": None}
    return {
        "status": "known", "trade_date": row["trade_date"],
        "receipt": finite(row["receipt"]), "change": finite(row["change_value"]),
        "source": row["source"], "fetched_at": row["fetched_at"],
    }


def enrich_item(db: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_code(db, str(item["code"]))
    price = finite(item.get("price"))
    ma5 = moving_average(rows, "close", 5)
    ma10 = moving_average(rows, "close", 10)
    ma20 = moving_average(rows, "close", 20)
    recent20 = rows[-20:]
    highs = [finite(row.get("high")) for row in recent20]
    lows = [finite(row.get("low")) for row in recent20]
    high20 = max((value for value in highs if value is not None), default=None)
    low20 = min((value for value in lows if value is not None), default=None)
    position20 = None
    if price is not None and high20 is not None and low20 is not None and high20 > low20:
        position20 = (price - low20) / (high20 - low20) * 100
    volume20 = moving_average(rows, "volume", 20)
    current_volume = finite(item.get("volume"))
    volume_ratio = current_volume / volume20 if current_volume is not None and volume20 else None
    structure = structure_state(rows, price)
    trend = trend_label(price, ma5, ma10, ma20)
    support = low20
    resistance = high20
    atr = atr14(rows)
    if price is not None and atr is not None:
        support = max(low20, price - 1.5 * atr) if low20 is not None else price - 1.5 * atr
        resistance = min(high20, price + 1.5 * atr) if high20 is not None else price + 1.5 * atr
    tick = finite(item.get("tick"))
    result = dict(item)
    result.update({
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "atr14": atr,
        "range_20d_high": high20, "range_20d_low": low20,
        "range_20d_position_pct": position20, "volume_ratio_20d": volume_ratio,
        "support": tick_round(support, tick), "resistance": tick_round(resistance, tick),
        "invalidation": tick_round(support - atr * 0.5, tick) if support is not None and atr is not None else None,
        "trend_state": trend, **structure, "fvg": fvg_state(rows),
        "warehouse_receipt": latest_receipt(db, str(item["code"])),
    })
    result["signal_label"] = action_label(str(item.get("capital_state") or ""), str(result["structure"]), trend)
    return result


def build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    known_change = [item for item in items if finite(item.get("change_pct")) is not None]
    known_oi = [item for item in items if finite(item.get("open_interest_change_pct")) is not None]
    strongest = max(known_change, key=lambda row: finite(row.get("change_pct")) or -math.inf, default=None)
    weakest = min(known_change, key=lambda row: finite(row.get("change_pct")) or math.inf, default=None)
    oi_in = max(known_oi, key=lambda row: finite(row.get("open_interest_change_pct")) or -math.inf, default=None)
    oi_out = min(known_oi, key=lambda row: finite(row.get("open_interest_change_pct")) or math.inf, default=None)
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("capital_state") or "等待量仓确认")
        counts[key] = counts.get(key, 0) + 1
    ranking = sorted(
        items,
        key=lambda row: (
            finite(row.get("change_pct")) or 0,
            finite(row.get("open_interest_change_pct")) or 0,
            finite(row.get("volume_ratio_20d")) or 0,
        ),
        reverse=True,
    )
    return {
        "strongest": compact_leader(strongest), "weakest": compact_leader(weakest),
        "largest_oi_increase": compact_leader(oi_in), "largest_oi_decrease": compact_leader(oi_out),
        "capital_counts": counts, "ranking": [str(item.get("code")) for item in ranking],
    }


def compact_leader(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "code": item.get("code"), "name": item.get("name"),
        "change_pct": finite(item.get("change_pct")),
        "open_interest_change_pct": finite(item.get("open_interest_change_pct")),
        "capital_state": item.get("capital_state"),
    }
