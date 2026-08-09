from __future__ import annotations

import importlib
import runpy
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import get_type_hints

import pytest

from scripts.us_compass_research_metrics import (
    COST_SCENARIOS,
    COST_UNAVAILABLE_REASON,
    annualized_volatility,
    finite_numbers,
    max_drawdown,
    pearson,
    ranks,
    rate_time_slice_audit,
    rate_shadow_health,
    rolling_slices,
    shadow_health_score,
    spearman,
)


def test_shadow_provenance_constants_and_score_are_shared():
    assert COST_SCENARIOS == (0, 0.0005, 0.001, 0.002, 0.003)
    assert COST_UNAVAILABLE_REASON == "turnover history unavailable; exact cost scenarios require persisted turnover"
    assert shadow_health_score(0.2, 0.6) == pytest.approx(0.8)
    assert shadow_health_score(-0.2, 0.6) == pytest.approx(0.3)


@pytest.mark.parametrize("total_return,positive_rate", [(True, .5), (float("nan"), .5), (0, True), (0, -0.1), (0, 1.1)])
def test_shadow_health_score_rejects_invalid_inputs(total_return, positive_rate):
    with pytest.raises(ValueError):
        shadow_health_score(total_return, positive_rate)


def test_ranks_use_average_rank_for_ties():
    assert ranks([30.0, 10.0, 20.0, 20.0]) == [4.0, 1.0, 2.5, 2.5]


def test_ranks_accept_empty_input():
    assert ranks([]) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_ranks_reject_non_finite_values(value):
    with pytest.raises(ValueError, match="values must be finite"):
        ranks([1.0, value, 2.0])


def test_spearman_correlates_average_ranks():
    assert spearman(
        [10.0, 20.0, 20.0, 30.0], [4.0, 1.0, 2.0, 3.0]
    ) == pytest.approx(-0.31622776601683794)


def test_pearson_returns_linear_correlation():
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


@pytest.mark.parametrize("metric", [pearson, spearman])
def test_correlations_reject_mismatched_lengths(metric):
    assert metric([1.0, 2.0, 3.0], [1.0, 2.0]) is None


@pytest.mark.parametrize("metric", [pearson, spearman])
def test_correlations_reject_zero_variance(metric):
    assert metric([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


@pytest.mark.parametrize("metric", [pearson, spearman])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_correlations_reject_non_finite_inputs(metric, value):
    assert metric([1.0, value, 3.0], [1.0, 2.0, 3.0]) is None


def test_pearson_returns_none_when_intermediate_calculation_overflows():
    assert pearson([1e308, -1e308, 1e308], [1.0, 2.0, 3.0]) is None


def test_max_drawdown_measures_worst_peak_to_trough_loss():
    assert max_drawdown([100.0, 120.0, 90.0, 108.0, 80.0]) == pytest.approx(-1 / 3)


def test_max_drawdown_returns_none_for_empty_input():
    assert max_drawdown([]) is None


@pytest.mark.parametrize(
    "equity",
    [
        [100.0, 0.0, 90.0],
        [100.0, -1.0, 90.0],
        [100.0, float("nan"), 90.0],
        [100.0, float("inf"), 90.0],
        [100.0, float("-inf"), 90.0],
    ],
)
def test_max_drawdown_rejects_non_positive_or_non_finite_equity(equity):
    assert max_drawdown(equity) is None


def test_annualized_volatility_uses_sample_standard_deviation():
    assert annualized_volatility([0.01, -0.01], periods_per_year=252) == pytest.approx(
        0.02 / (2 ** 0.5) * (252 ** 0.5)
    )


@pytest.mark.parametrize("returns", [[], [0.01]])
def test_annualized_volatility_rejects_insufficient_samples(returns):
    assert annualized_volatility(returns) is None


@pytest.mark.parametrize("periods_per_year", [0, -1])
def test_annualized_volatility_rejects_non_positive_periods(periods_per_year):
    assert annualized_volatility([0.01, -0.01], periods_per_year) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_annualized_volatility_rejects_non_finite_inputs(value):
    assert annualized_volatility([0.01, value]) is None


def test_annualized_volatility_returns_none_when_calculation_overflows():
    assert annualized_volatility([1e308, -1e308]) is None


def test_rolling_slices_returns_contiguous_complete_windows():
    assert rolling_slices([1, 2, 3, 4], 3) == [[1, 2, 3], [2, 3, 4]]


def test_rolling_slices_returns_empty_when_window_exceeds_sequence():
    assert rolling_slices([1, 2], 3) == []


@pytest.mark.parametrize("window", [0, -1])
def test_rolling_slices_rejects_non_positive_window(window):
    with pytest.raises(ValueError, match="window must be positive"):
        rolling_slices([1, 2], window)


def test_finite_numbers_filters_invalid_and_non_finite_values():
    assert finite_numbers([1, "2.5", None, "bad", float("nan"), float("inf")]) == [
        1.0,
        2.5,
    ]


@pytest.mark.parametrize(
    ("observations", "rate", "icir", "minimum", "expected"),
    [
        (19, 1.0, 1.0, 20, "ACCUMULATING"),
        (20, None, 1.0, 20, "ACCUMULATING"),
        (20, 0.4999, 1.0, 20, "FRAGILE"),
        (20, 0.5, 1.0, 20, "MIXED"),
        (20, 0.6999, 1.0, 20, "MIXED"),
        (20, 0.7, 0.0, 20, "MIXED"),
        (20, 0.7, None, 20, "MIXED"),
        (20, 0.7, 0.1, 20, "STABLE"),
        (9, 1.0, 1.0, 10, "ACCUMULATING"),
        (10, 1.0, 1.0, 10, "STABLE"),
    ],
)
def test_rate_time_slice_audit_exact_boundaries(
    observations, rate, icir, minimum, expected
):
    assert rate_time_slice_audit(observations, rate, icir, minimum) == expected


@pytest.mark.parametrize(
    ("observations", "rate", "icir", "minimum"),
    [
        (True, 0.7, 0.1, 20),
        (20.0, 0.7, 0.1, 20),
        (-1, 0.7, 0.1, 20),
        (20, True, 0.1, 20),
        (20, -0.1, 0.1, 20),
        (20, 1.1, 0.1, 20),
        (20, float("nan"), 0.1, 20),
        (20, 0.7, True, 20),
        (20, 0.7, float("inf"), 20),
        (20, 0.7, 0.1, True),
        (20, 0.7, 0.1, 0),
    ],
)
def test_rate_time_slice_audit_rejects_invalid_inputs(
    observations, rate, icir, minimum
):
    with pytest.raises(ValueError):
        rate_time_slice_audit(observations, rate, icir, minimum)


@pytest.mark.parametrize(
    ("observations", "total_return", "drawdown", "positive_rate", "expected"),
    [
        (19, 1.0, 0.0, 1.0, "ACCUMULATING"),
        (20, 0.0001, 0.15, 0.55, "STABLE"),
        (20, 0.0001, 0.150001, 0.55, "MIXED"),
        (20, 0.0, 0.9, 0.0, "MIXED"),
        (20, -0.1, 0.9, 0.5, "MIXED"),
        (20, -0.1, 0.9, 0.499999, "FRAGILE"),
    ],
)
def test_rate_shadow_health_exact_boundaries(
    observations, total_return, drawdown, positive_rate, expected
):
    assert rate_shadow_health(
        observations, total_return, drawdown, positive_rate
    ) == expected


@pytest.mark.parametrize(
    "function_name, parameter_name",
    [
        ("ranks", "values"),
        ("annualized_volatility", "returns"),
        ("max_drawdown", "values"),
        ("pearson", "xs"),
        ("pearson", "ys"),
        ("spearman", "xs"),
        ("spearman", "ys"),
    ],
)
def test_read_only_metric_inputs_are_sequences(function_name, parameter_name):
    module = importlib.import_module("scripts.us_compass_research_metrics")
    hints = get_type_hints(getattr(module, function_name))
    assert hints[parameter_name] == Sequence[float]


def test_learning_module_package_import_uses_relative_metrics(monkeypatch):
    fake = types.ModuleType("us_compass_research_metrics")

    def fake_ranks(values):
        return values

    def fake_spearman(xs, ys):
        return None

    setattr(fake, "ranks", fake_ranks)
    setattr(fake, "spearman", fake_spearman)
    monkeypatch.setitem(sys.modules, "us_compass_research_metrics", fake)
    sys.modules.pop("scripts.update_us_compass_learning", None)

    module = importlib.import_module("scripts.update_us_compass_learning")

    assert module.ranks is not fake_ranks
    assert module.spearman is not fake_spearman
    assert module.ranks.__module__ == "scripts.us_compass_research_metrics"


def test_learning_script_direct_load_uses_sibling_metrics(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/update_us_compass_learning.py"
    fake = types.ModuleType("us_compass_research_metrics")

    def fake_ranks(values):
        return [999.0] * len(values)

    def fake_spearman(xs, ys):
        return 999.0

    setattr(fake, "ranks", fake_ranks)
    setattr(fake, "spearman", fake_spearman)
    monkeypatch.setitem(sys.modules, "us_compass_research_metrics", fake)
    original_path = sys.path.copy()

    namespace = runpy.run_path(str(script), run_name="us_compass_learning_direct_import")

    assert namespace["ranks"]([30.0, 10.0, 20.0]) == [3.0, 1.0, 2.0]
    assert namespace["spearman"]([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    assert namespace["ranks"] is not fake_ranks
    assert namespace["spearman"] is not fake_spearman
    assert namespace["ranks"].__module__.startswith("_us_compass_research_metrics_")
    assert sys.modules["us_compass_research_metrics"] is fake
    assert sys.path == original_path
