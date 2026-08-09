from __future__ import annotations

import copy
import importlib.util
import json
import math
import statistics
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_us_compass_health.py"
VALIDATOR = ROOT / "scripts" / "validate_public_data_contracts.py"


def load_module(path=SCRIPT, name="generate_us_compass_health"):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fingerprint():
    return {
        "model_version": "us-compass-v1", "universe_count": 2,
        "symbols_sha256": "a" * 64, "config_sha256": "b" * 64,
        "execution_basis": "T close; T+1 open", "one_way_cost": 0.001,
        "initial_capital": 20_000.0, "horizons": [1, 5, 20],
        "exposure_mapping": {"values": {"strong": 1.0, "weak": 0.0}, "default": 0.5},
    }


def learning_with(values_by_horizon, fingerprint):
    steps = {name: int(name[1:]) for name in values_by_horizon}
    length = max((len(values) + steps[name] for name, values in values_by_horizon.items()), default=1)
    dates = [(date(2026, 1, 1) + timedelta(days=index)).isoformat() for index in range(length)]
    snapshots = []
    for index, signal_date in enumerate(dates):
        outcomes = {}
        for horizon, values in values_by_horizon.items():
            if index < len(values):
                outcomes[horizon] = {"rank_ic": values[index], "end_date": dates[index + steps[horizon]]}
        snapshots.append({"date": signal_date, "outcomes": outcomes})
    return {"snapshots": snapshots, "model_fingerprint": copy.deepcopy(fingerprint)}


def shadow_with(fingerprint):
    return {
        "model_fingerprint": copy.deepcopy(fingerprint),
        "initial_capital_usd": 20_000.0,
        "one_way_cost": 0.001,
        "history": [],
    }


def shadow_history(count, fingerprint, returns=None):
    returns = returns or [0.01] * count
    history = []
    for index, period_return in enumerate(returns):
        history.append({
            "signal_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
            "entry_date": (date(2026, 1, 2) + timedelta(days=index)).isoformat(),
            "exit_date": (date(2026, 1, 3) + timedelta(days=index)).isoformat(),
            "exposure": "neutral",
            "returns": {name: period_return for name in ("benchmark", "timing", "rotation", "fusion")},
        })
    result = shadow_with(fingerprint)
    result["history"] = history
    return result


def point(index, value):
    return {
        "signal_date": (date(2025, 12, 1) + timedelta(days=index)).isoformat(),
        "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
        "value": value,
    }


def test_extract_rank_ic_series_sorts_snapshots_by_signal_date_and_uses_end_date(fingerprint):
    learning = learning_with({"t5": [0.1, -0.2]}, fingerprint)
    learning["snapshots"] = list(reversed(learning["snapshots"]))
    assert load_module().extract_rank_ic_series(learning, "t5") == [
        {"signal_date": "2026-01-01", "date": "2026-01-06", "value": 0.1},
        {"signal_date": "2026-01-02", "date": "2026-01-07", "value": -0.2},
    ]


def test_build_rejects_duplicate_snapshot_dates_even_without_outcomes(fingerprint):
    learning = learning_with({"t5": [0.1]}, fingerprint)
    learning["snapshots"].append({"date": "2026-01-01", "outcomes": {}})
    with pytest.raises(ValueError, match="duplicate snapshot date"):
        load_module().build_health_payload(learning, shadow_with(fingerprint), "2026-02-01T00:00:00Z")


@pytest.mark.parametrize("snapshots,message,horizon", [
    ([{"date": "2026-01-01", "outcomes": {}}, {"date": "2026-01-01", "outcomes": {}}], "duplicate", "t1"),
    ([{"date": "bad", "outcomes": {}}], "date", "t1"),
    ([{"date": "2026-01-01", "outcomes": {"t1": {"end_date": "2026-01-02", "rank_ic": float("nan")}}}, {"date": "2026-01-02", "outcomes": {}}], "finite", "t1"),
    ([{"date": "2026-01-01", "outcomes": {"t1": {"end_date": "2026-01-02", "rank_ic": True}}}, {"date": "2026-01-02", "outcomes": {}}], "finite", "t1"),
])
def test_extract_rank_ic_series_rejects_malformed_points(snapshots, message, horizon):
    with pytest.raises(ValueError, match=message):
        load_module().extract_rank_ic_series({"snapshots": snapshots}, horizon)


@pytest.mark.parametrize(("outcome", "message"), [
    ({"rank_ic": 0.1}, "end_date"),
    ({"rank_ic": 0.1, "end_date": "bad"}, "end_date"),
    ({"rank_ic": 0.1, "end_date": "2026-01-03"}, "expected 2026-01-02"),
])
def test_extract_rejects_missing_malformed_or_mismatched_end_date(outcome, message):
    learning = {"snapshots": [
        {"date": "2026-01-01", "outcomes": {"t1": outcome}},
        {"date": "2026-01-02", "outcomes": {}},
    ]}
    with pytest.raises(ValueError, match=message):
        load_module().extract_rank_ic_series(learning, "t1")


def test_extract_rejects_outcome_without_future_snapshot():
    learning = {"snapshots": [{
        "date": "2026-01-01",
        "outcomes": {"t5": {"rank_ic": 0.1, "end_date": "2026-01-06"}},
    }]}
    with pytest.raises(ValueError, match="future snapshot"):
        load_module().extract_rank_ic_series(learning, "t5")


def test_immature_horizon_nulls_statistics_but_preserves_series():
    series = [point(0, 0.2)]
    result = load_module().horizon_health(series, initial=20, stable=40)
    assert result == {
        "status": "ACCUMULATING", "observations": 1, "minimum_required": 20,
        "maturity_ratio": 0.05, "rank_ic_mean": None, "rank_ic_median": None,
        "rank_ic_std": None, "icir": None, "positive_rate": None,
        "recent_5_mean": None, "recent_5_count": 0, "recent_10_mean": None,
        "recent_10_count": 0, "trend": None, "series": series,
    }
    assert result["series"] is not series


def test_mature_horizon_computes_exact_statistics_and_recent_actual_counts():
    values = [0.1, -0.2, 0.3, 0.4, -0.1] * 4
    result = load_module().horizon_health([point(i, value) for i, value in enumerate(values)], 20, 40)
    assert result["status"] == "MIXED"
    assert result["observations"] == 20 and result["maturity_ratio"] == 1
    assert result["rank_ic_mean"] == pytest.approx(statistics.fmean(values))
    assert result["rank_ic_median"] == pytest.approx(statistics.median(values))
    assert result["rank_ic_std"] == pytest.approx(statistics.stdev(values))
    assert result["icir"] == pytest.approx(statistics.fmean(values) / statistics.stdev(values))
    assert result["positive_rate"] == pytest.approx(0.6)
    assert result["recent_5_count"] == 5 and result["recent_10_count"] == 10
    assert result["recent_5_mean"] == pytest.approx(statistics.fmean(values[-5:]))
    assert result["series"][-1]["value"] == -0.1


def test_zero_standard_deviation_has_null_icir():
    result = load_module().horizon_health([point(i, 0.1) for i in range(20)], 20, 40)
    assert result["rank_ic_std"] == 0 and result["icir"] is None


@pytest.mark.parametrize(
    ("count", "expected_observations", "expected_statuses"),
    [
        (0, [], []),
        (4, [4], ["INSUFFICIENT"]),
        (5, [5], ["POSITIVE"]),
        (6, [5, 1], ["POSITIVE", "INSUFFICIENT"]),
        (10, [5, 5], ["POSITIVE", "POSITIVE"]),
        (11, [5, 5, 1], ["POSITIVE", "POSITIVE", "INSUFFICIENT"]),
    ],
)
def test_time_slices_partition_consecutively_without_overlap(
    count, expected_observations, expected_statuses
):
    series = [point(index, 0.1) for index in range(count)]

    slices = load_module().build_time_slices(series)

    assert [item["observations"] for item in slices] == expected_observations
    assert [item["status"] for item in slices] == expected_statuses
    assert [item["index"] for item in slices] == list(range(len(slices)))
    assigned = []
    for item in slices:
        assigned.extend(
            p["date"]
            for p in series
            if item["start_date"] <= p["date"] <= item["end_date"]
        )
    assert assigned == [p["date"] for p in series]
    assert len(assigned) == len(set(assigned))


def test_time_slice_metrics_and_remainder_null_results():
    values = [0.2, -0.1, 0.0, 0.3, -0.2, 0.9]

    slices = load_module().build_time_slices(
        [point(index, value) for index, value in enumerate(values)]
    )

    assert slices[0]["mean"] == pytest.approx(0.04)
    assert slices[0]["positive_rate"] == pytest.approx(0.4)
    assert slices[0]["status"] == "POSITIVE"
    assert slices[0]["start_date"] == "2026-01-01"
    assert slices[0]["end_date"] == "2026-01-05"
    assert slices[0]["signal_start_date"] == "2025-12-01"
    assert slices[0]["signal_end_date"] == "2025-12-05"
    assert slices[1]["status"] == "INSUFFICIENT"
    assert slices[1]["mean"] is None and slices[1]["positive_rate"] is None


@pytest.mark.parametrize("size", [0, -1, True, 4, 6, 5.0, "5"])
def test_build_time_slices_only_accepts_governing_slice_size(size):
    with pytest.raises(ValueError, match="^slice size must be 5$"):
        load_module().build_time_slices([point(0, 0.1)], size)


@pytest.mark.parametrize(
    ("series", "message"),
    [
        (None, "series must be a list"),
        ([None], "point 0 must be an object"),
        ([{"signal_date": "2025-12-01", "date": "2026-01-01"}], "exactly"),
        ([{"signal_date": "bad", "date": "2026-01-01", "value": 0.1}], "signal_date"),
        ([{"signal_date": "2025-12-01", "date": "bad", "value": 0.1}], "date"),
        ([{"signal_date": "2026-01-01", "date": "2026-01-01", "value": 0.1}], "before"),
        ([{"signal_date": "2025-12-01", "date": "2026-01-01", "value": True}], "finite"),
        ([{"signal_date": "2025-12-01", "date": "2026-01-01", "value": float("nan")}], "finite"),
        ([{"signal_date": "2025-12-01", "date": "2026-01-01", "value": float("inf")}], "finite"),
    ],
)
def test_build_time_slices_rejects_malformed_series_before_statistics(series, message):
    with pytest.raises(ValueError, match=message):
        load_module().build_time_slices(series)


@pytest.mark.parametrize("field", ["signal_date", "date"])
@pytest.mark.parametrize("mode", ["duplicate", "descending"])
def test_build_time_slices_requires_unique_strictly_ascending_dates(field, mode):
    series = [point(0, 0.1), point(1, 0.2)]
    if mode == "duplicate":
        series[1][field] = series[0][field]
    else:
        series = list(reversed(series))

    with pytest.raises(ValueError, match=f"{field}s must be unique and strictly ascending"):
        load_module().build_time_slices(series)


@pytest.mark.parametrize(
    ("observations", "rate", "icir", "expected"),
    [
        (19, 1.0, 1.0, "ACCUMULATING"),
        (20, 0.4999, 1.0, "FRAGILE"),
        (20, 0.5, 1.0, "MIXED"),
        (20, 0.6999, 1.0, "MIXED"),
        (20, 0.7, -0.1, "MIXED"),
        (20, 0.7, None, "MIXED"),
        (20, 0.7, 0.1, "STABLE"),
    ],
)
def test_time_slice_rating_boundaries(observations, rate, icir, expected):
    assert load_module().rate_time_slice_audit(observations, rate, icir) == expected


def test_walk_forward_aggregates_evaluated_slices_and_rate():
    values = [0.2] * 5 + [-0.2] * 5 + [0.1] * 5 + [0.3] * 5 + [0.7]
    t5 = load_module().horizon_health(
        [point(index, value) for index, value in enumerate(values)], 20, 40
    )

    result = load_module().build_walk_forward(t5)

    assert result["horizon"] == "t5" and result["slice_size"] == 5
    assert result["windows"] == 5 and result["evaluated_windows"] == 4
    assert result["positive_windows"] == 3
    assert result["positive_slice_rate"] == pytest.approx(0.75)
    assert result["score"] == pytest.approx(0.75)
    assert result["status"] == "STABLE"
    assert result["slices"][-1]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("recent,expected", [([0.02] * 5, "IMPROVING"), ([0.0] * 5, "WEAKENING"), ([0.014] * 5, "FLAT")])
def test_trend_compares_recent_five_with_prior_five(recent, expected):
    values = [0.01] * 15 + recent
    assert load_module().horizon_health([point(i, v) for i, v in enumerate(values)], 20, 40)["trend"] == expected


def test_thresholds_and_governing_maturity_use_t5(fingerprint):
    learning = learning_with({"t1": [0.1] * 20, "t5": [0.1] * 19, "t20": [0.1] * 12}, fingerprint)
    payload = load_module().build_health_payload(learning, shadow_with(fingerprint), "2026-02-01T00:00:00Z")
    assert payload["horizons"]["t1"]["status"] == "MIXED"
    assert payload["horizons"]["t5"]["status"] == "ACCUMULATING"
    assert payload["horizons"]["t20"]["status"] == "MIXED"
    assert payload["sample_maturity"] == {
        "status": "ACCUMULATING", "observations": 19, "minimum_observations": 20,
        "mature": False, "reasons": ["T+5 requires 20 observations; 19 available"],
    }
    assert payload["overall"]["status"] == "ACCUMULATING" and payload["overall"]["score"] is None
    validator = load_module(VALIDATOR, "threshold_health_validator")
    assert validator.validate_us_compass_health_payload(payload) == []


def test_stable_t5_governs_overall_with_bounded_score(fingerprint):
    positive_variable = [0.1, 0.2] * 20
    learning = learning_with({"t1": positive_variable, "t5": positive_variable, "t20": [0.1, 0.2] * 10}, fingerprint)
    payload = load_module().build_health_payload(learning, shadow_with(fingerprint), "2026-03-01T00:00:00Z")
    assert payload["sample_maturity"]["status"] == "STABLE"
    assert payload["walk_forward"]["status"] == "STABLE"
    assert payload["walk_forward"]["windows"] == 8
    assert payload["walk_forward"]["evaluated_windows"] == 8
    assert payload["walk_forward"]["positive_windows"] == 8
    assert payload["walk_forward"]["positive_slice_rate"] == 1
    assert payload["overall"]["status"] == payload["walk_forward"]["status"]
    assert payload["overall"]["score"] == payload["walk_forward"]["score"] == 1


def test_extract_shadow_history_sorts_dates_and_compounds_returns(fingerprint):
    shadow = shadow_history(3, fingerprint, returns=[0.1, -0.25, 0.2])
    shadow["history"] = list(reversed(shadow["history"]))
    extracted = load_module().extract_shadow_history(shadow)
    assert [row["date"] for row in extracted] == ["2026-01-03", "2026-01-04", "2026-01-05"]
    assert extracted[0]["returns"]["fusion"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.append(None), "must be an object"),
        (lambda rows: rows[0].update(exit_date="bad"), "exit_date"),
        (lambda rows: rows[1].update(exit_date=rows[0]["exit_date"]), "duplicate"),
        (lambda rows: rows[0]["returns"].update(fusion=float("nan")), "finite"),
    ],
)
def test_extract_shadow_history_rejects_malformed_rows(fingerprint, mutate, message):
    shadow = shadow_history(2, fingerprint)
    mutate(shadow["history"])
    with pytest.raises(ValueError, match=message):
        load_module().extract_shadow_history(shadow)


def test_portfolio_metrics_use_initial_capital_and_positive_drawdown_magnitude(fingerprint):
    returns = [0.2, -0.25, 0.2, 0.0] + [0.0] * 16
    rows = load_module().extract_shadow_history(shadow_history(20, fingerprint, returns=returns))
    result = load_module().portfolio_health_metrics(rows, 20_000.0, "fusion")
    assert result["total_return"] == pytest.approx(0.08)
    assert result["max_drawdown"] == pytest.approx(0.25)
    assert result["current_drawdown"] == pytest.approx(0.1)
    assert result["longest_drawdown_duration"] == 19
    assert result["positive_period_rate"] == pytest.approx(0.1)
    assert result["rolling_20d_volatility"] is not None
    assert result["equity_series"][0]["equity"] == pytest.approx(24_000.0)


def test_cost_sensitivity_fails_closed_without_turnover_history(fingerprint):
    shadow = shadow_history(20, fingerprint, returns=[0.01] * 20)
    result = load_module().build_cost_sensitivity(shadow, "STABLE")
    values = [scenario["value"] for scenario in result["scenarios"]]
    assert result["observations"] == 20 and result["baseline_cost"] == 0.001
    assert result["status"] == "UNAVAILABLE"
    assert values == [None] * 5
    assert result["reasons"] == ["turnover history unavailable; exact cost scenarios require persisted turnover"]


def test_shadow_and_cost_sections_gate_on_their_own_observations(fingerprint):
    payload = load_module().build_health_payload(
        learning_with({"t5": [0.1]}, fingerprint),
        shadow_history(20, fingerprint),
        "2026-03-01T00:00:00Z",
    )
    assert payload["sample_maturity"]["status"] == "ACCUMULATING"
    assert payload["shadow_health"]["status"] == "STABLE"
    assert payload["shadow_health"]["observations"] == 20
    assert payload["shadow_health"]["portfolios"]["fusion"]["total_return"] is not None
    assert payload["cost_sensitivity"]["status"] == "UNAVAILABLE"
    assert payload["cost_sensitivity"]["scenarios"][0]["value"] is None
    assert payload["overall"]["status"] == "ACCUMULATING"


def test_mature_overall_uses_slice_rating_not_horizon_status(fingerprint):
    values = [-0.1] * 5 + [0.1] * 15
    payload = load_module().build_health_payload(
        learning_with({"t5": values}, fingerprint),
        shadow_with(fingerprint),
        "2026-03-01T00:00:00Z",
    )

    assert payload["horizons"]["t5"]["status"] == "MIXED"
    assert payload["walk_forward"]["status"] == "STABLE"
    assert payload["sample_maturity"]["status"] == "STABLE"
    assert payload["overall"]["status"] == "STABLE"
    assert payload["overall"]["score"] == pytest.approx(0.75)


def test_full_payload_validates_and_cli_writes_atomically(tmp_path, fingerprint):
    learning = learning_with({"t1": [0.1] * 20, "t5": [0.1] * 20, "t20": [0.1] * 12}, fingerprint)
    learning_path, shadow_path, output = tmp_path / "learning.json", tmp_path / "shadow.json", tmp_path / "health.json"
    learning_path.write_text(json.dumps(learning), encoding="utf-8")
    shadow_path.write_text(json.dumps(shadow_with(fingerprint)), encoding="utf-8")
    completed = subprocess.run([
        str(ROOT / ".build-venv/bin/python"), str(SCRIPT), "--learning", str(learning_path),
        "--shadow", str(shadow_path), "--output", str(output),
        "--generated-at", "2026-03-01T00:00:00Z",
    ], cwd=ROOT, text=True, capture_output=True, check=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    validator = load_module(VALIDATOR, "health_validator")
    assert validator.validate_us_compass_health_payload(payload) == []
    assert payload["generated_at"] == "2026-03-01T00:00:00Z"
    assert "health generated" in completed.stdout and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("learning,message", [
    ([], "learning"), ({"snapshots": []}, "snapshots"),
    ({"snapshots": [{"date": "2026-01-01", "outcomes": {}}]}, "model_fingerprint"),
    ({"snapshots": [{"date": "2026-01-01", "outcomes": {}}], "model_fingerprint": {}}, "model_fingerprint"),
])
def test_build_rejects_malformed_or_empty_learning(learning, message, fingerprint):
    with pytest.raises(ValueError, match=message):
        load_module().build_health_payload(learning, shadow_with(fingerprint), "2026-02-01T00:00:00Z")


def test_build_requires_matching_learning_and_shadow_fingerprints(fingerprint):
    learning = learning_with({"t5": [0.1]}, fingerprint)
    mismatched = shadow_with(fingerprint)
    mismatched["model_fingerprint"]["config_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="learning/shadow model fingerprint mismatch"):
        load_module().build_health_payload(learning, mismatched, "2026-02-01T00:00:00Z")


def test_generated_payload_contains_no_nonfinite_numbers(fingerprint):
    payload = load_module().build_health_payload(
        learning_with({"t1": [0.1] * 20, "t5": [0.1] * 20, "t20": []}, fingerprint),
        shadow_with(fingerprint), "2026-02-01T00:00:00Z")
    def walk(value):
        if isinstance(value, dict):
            for item in value.values(): walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, float):
            assert math.isfinite(value)
    walk(payload)


def test_build_rejects_invalid_generated_at(fingerprint):
    learning = learning_with({"t5": [0.1]}, fingerprint)
    with pytest.raises(ValueError, match="generated_at"):
        load_module().build_health_payload(learning, shadow_with(fingerprint), "2026-02-01")


def test_default_cli_fails_with_staging_blocker_and_writes_no_output(tmp_path):
    output = tmp_path / "health.json"
    completed = subprocess.run(
        [str(ROOT / ".build-venv/bin/python"), str(SCRIPT), "--output", str(output)],
        cwd=ROOT, text=True, capture_output=True)
    assert completed.returncode != 0
    message = completed.stdout + completed.stderr
    assert "staging blocker" in message
    assert "must be regenerated by update_us_compass_learning.py to add matching model_fingerprint values" in message
    assert not output.exists()


def test_explicit_cli_read_error_is_concise_and_writes_no_output(tmp_path, fingerprint):
    missing = tmp_path / "missing-learning.json"
    shadow_path = tmp_path / "shadow.json"
    output = tmp_path / "health.json"
    shadow_path.write_text(json.dumps(shadow_with(fingerprint)), encoding="utf-8")
    completed = subprocess.run([
        str(ROOT / ".build-venv/bin/python"), str(SCRIPT),
        "--learning", str(missing), "--shadow", str(shadow_path), "--output", str(output),
    ], cwd=ROOT, text=True, capture_output=True)
    message = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert f"cannot read learning payload: {missing}" in message
    assert "Traceback" not in message
    assert not output.exists()
