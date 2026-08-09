from __future__ import annotations

import pytest

from scripts.us_compass_research_metrics import (
    annualized_volatility,
    finite_numbers,
    max_drawdown,
    pearson,
    ranks,
    rolling_slices,
    spearman,
)


def test_ranks_use_average_rank_for_ties():
    assert ranks([30.0, 10.0, 20.0, 20.0]) == [4.0, 1.0, 2.5, 2.5]


def test_spearman_correlates_average_ranks():
    assert spearman([10.0, 20.0, 20.0, 30.0], [4.0, 1.0, 2.0, 3.0]) == -0.31622776601683794


def test_pearson_returns_linear_correlation():
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


def test_max_drawdown_measures_worst_peak_to_trough_loss():
    assert max_drawdown([100.0, 120.0, 90.0, 108.0, 80.0]) == pytest.approx(-1 / 3)


def test_annualized_volatility_uses_sample_standard_deviation():
    assert annualized_volatility([0.01, -0.01], periods_per_year=252) == pytest.approx(
        0.02 / (2 ** 0.5) * (252 ** 0.5)
    )


def test_rolling_slices_returns_contiguous_complete_windows():
    assert rolling_slices([1, 2, 3, 4], 3) == [[1, 2, 3], [2, 3, 4]]


def test_finite_numbers_filters_invalid_and_non_finite_values():
    assert finite_numbers([1, "2.5", None, "bad", float("nan"), float("inf")]) == [
        1.0,
        2.5,
    ]
