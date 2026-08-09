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


def ranks(values: Sequence[float]) -> list[float]:
    """Return ascending one-based average ranks, preserving input order."""
    if any(not math.isfinite(value) for value in values):
        raise ValueError("values must be finite")
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


def rate_time_slice_audit(
    mature_observations: int,
    positive_slice_rate: float | None,
    t5_icir: float | None,
    minimum_observations: int = 20,
) -> str:
    """Rate credibility from a governing non-overlapping time-slice audit."""
    if (
        isinstance(mature_observations, bool)
        or not isinstance(mature_observations, int)
        or mature_observations < 0
    ):
        raise ValueError("mature_observations must be a non-negative integer")
    if (
        isinstance(minimum_observations, bool)
        or not isinstance(minimum_observations, int)
        or minimum_observations <= 0
    ):
        raise ValueError("minimum_observations must be a positive integer")
    if positive_slice_rate is not None and (
        isinstance(positive_slice_rate, bool)
        or not isinstance(positive_slice_rate, (int, float))
        or not math.isfinite(positive_slice_rate)
        or not 0 <= positive_slice_rate <= 1
    ):
        raise ValueError("positive_slice_rate must be null or finite in [0, 1]")
    if t5_icir is not None and (
        isinstance(t5_icir, bool)
        or not isinstance(t5_icir, (int, float))
        or not math.isfinite(t5_icir)
    ):
        raise ValueError("t5_icir must be null or finite")
    if mature_observations < minimum_observations or positive_slice_rate is None:
        return "ACCUMULATING"
    if positive_slice_rate < 0.5:
        return "FRAGILE"
    if positive_slice_rate < 0.7:
        return "MIXED"
    return "STABLE" if t5_icir is not None and t5_icir > 0 else "MIXED"


def rate_shadow_health(
    observations: int,
    total_return: float,
    max_drawdown_loss: float,
    positive_rate: float,
    minimum_observations: int = 20,
) -> str:
    """Rate mature shadow performance while failing closed on invalid inputs."""
    if isinstance(observations, bool) or not isinstance(observations, int) or observations < 0:
        raise ValueError("observations must be a non-negative integer")
    if isinstance(minimum_observations, bool) or not isinstance(minimum_observations, int) or minimum_observations <= 0:
        raise ValueError("minimum_observations must be a positive integer")
    for name, value in (
        ("total_return", total_return),
        ("max_drawdown_loss", max_drawdown_loss),
        ("positive_rate", positive_rate),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if max_drawdown_loss < 0:
        raise ValueError("max_drawdown_loss must be non-negative")
    if not 0 <= positive_rate <= 1:
        raise ValueError("positive_rate must be in [0, 1]")
    if observations < minimum_observations:
        return "ACCUMULATING"
    if total_return > 0 and max_drawdown_loss <= 0.15 and positive_rate >= 0.55:
        return "STABLE"
    if total_return >= 0 or positive_rate >= 0.5:
        return "MIXED"
    return "FRAGILE"


def annualized_volatility(
    returns: Sequence[float], periods_per_year: int = 252
) -> float | None:
    """Return annualized sample volatility for periodic returns."""
    if (
        len(returns) < 2
        or periods_per_year <= 0
        or any(not math.isfinite(value) for value in returns)
    ):
        return None
    try:
        result = statistics.stdev(returns) * math.sqrt(periods_per_year)
    except ArithmeticError:
        return None
    return result if math.isfinite(result) else None


def max_drawdown(values: Sequence[float]) -> float | None:
    """Return the worst peak-to-trough return in an equity series."""
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Return Pearson correlation, or ``None`` when undefined."""
    if (
        len(xs) < 2
        or len(xs) != len(ys)
        or any(not math.isfinite(value) for value in xs)
        or any(not math.isfinite(value) for value in ys)
    ):
        return None
    try:
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        dy = math.sqrt(sum((y - my) ** 2 for y in ys))
        result = numerator / (dx * dy) if dx and dy else None
    except ArithmeticError:
        return None
    return result if result is not None and math.isfinite(result) else None


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Return Spearman rank correlation, or ``None`` when undefined."""
    if (
        len(xs) < 3
        or len(xs) != len(ys)
        or any(not math.isfinite(value) for value in xs)
        or any(not math.isfinite(value) for value in ys)
    ):
        return None
    return pearson(ranks(xs), ranks(ys))
