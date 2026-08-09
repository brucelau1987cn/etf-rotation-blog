"""Pure research metric helpers for the US ETF Compass."""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def finite_numbers(values: Sequence[object]) -> list[float]:
    """Coerce values to floats and retain only finite numbers."""
    result = []
    for value in values:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def ranks(values: list[float]) -> list[float]:
    """Return ascending one-based average ranks, preserving input order."""
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2 + 1
        for position in range(start, end):
            result[order[position]] = rank
        start = end
    return result


def rolling_slices(values: Sequence[T], window: int) -> list[list[T]]:
    """Return all contiguous, complete slices of ``window`` items."""
    if window <= 0:
        raise ValueError("window must be positive")
    return [list(values[start : start + window]) for start in range(len(values) - window + 1)]


def annualized_volatility(
    returns: list[float], periods_per_year: int = 252
) -> float | None:
    """Return annualized sample volatility for periodic returns."""
    if len(returns) < 2 or periods_per_year <= 0:
        return None
    return statistics.stdev(returns) * math.sqrt(periods_per_year)


def max_drawdown(values: list[float]) -> float | None:
    """Return the worst peak-to-trough return in an equity series."""
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Return Pearson correlation, or ``None`` when undefined."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return numerator / (dx * dy) if dx and dy else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Return Spearman rank correlation, or ``None`` when undefined."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))
