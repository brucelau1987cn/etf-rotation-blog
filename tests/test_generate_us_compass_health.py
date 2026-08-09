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
    snapshots = []
    length = max((len(values) for values in values_by_horizon.values()), default=1)
    for index in range(length):
        outcomes = {}
        for horizon, values in values_by_horizon.items():
            if index < len(values):
                outcomes[horizon] = {"rank_ic": values[index]}
        snapshots.append({
            "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
            "outcomes": outcomes,
        })
    return {"snapshots": snapshots, "model_fingerprint": copy.deepcopy(fingerprint)}


def test_extract_rank_ic_series_sorts_snapshots_by_date():
    module = load_module()
    learning = {"snapshots": [
        {"date": "2026-01-03", "outcomes": {"t5": {"rank_ic": -0.2}}},
        {"date": "2026-01-01", "outcomes": {"t5": {"rank_ic": 0.1}}},
        {"date": "2026-01-02", "outcomes": {}},
    ]}
    assert module.extract_rank_ic_series(learning, "t5") == [
        {"date": "2026-01-01", "value": 0.1},
        {"date": "2026-01-03", "value": -0.2},
    ]


def test_build_rejects_duplicate_snapshot_dates_even_without_outcomes(fingerprint):
    learning = learning_with({"t5": [0.1]}, fingerprint)
    learning["snapshots"].append({"date": "2026-01-01", "outcomes": {}})
    with pytest.raises(ValueError, match="duplicate snapshot date"):
        load_module().build_health_payload(learning, "2026-02-01T00:00:00Z")


@pytest.mark.parametrize("snapshots,message", [
    ([{"date": "2026-01-01", "outcomes": {"t5": {"rank_ic": 0.1}}},
      {"date": "2026-01-01", "outcomes": {"t5": {"rank_ic": 0.2}}}], "duplicate"),
    ([{"date": "bad", "outcomes": {"t5": {"rank_ic": 0.1}}}], "date"),
    ([{"date": "2026-01-01", "outcomes": {"t5": {"rank_ic": float("nan")}}}], "finite"),
    ([{"date": "2026-01-01", "outcomes": {"t5": {"rank_ic": True}}}], "finite"),
])
def test_extract_rank_ic_series_rejects_malformed_points(snapshots, message):
    with pytest.raises(ValueError, match=message):
        load_module().extract_rank_ic_series({"snapshots": snapshots}, "t5")


def test_immature_horizon_nulls_all_statistics():
    result = load_module().horizon_health(
        [{"date": "2026-01-01", "value": 0.2}], initial=20, stable=40
    )
    assert result == {
        "status": "ACCUMULATING", "observations": 1, "minimum_required": 20,
        "maturity_ratio": 0.05, "rank_ic_mean": None, "rank_ic_median": None,
        "rank_ic_std": None, "icir": None, "positive_rate": None,
        "recent_5_mean": None, "recent_5_count": 0, "recent_10_mean": None,
        "recent_10_count": 0, "trend": None, "series": [],
    }


def test_mature_horizon_computes_exact_statistics_and_recent_actual_counts():
    values = [0.1, -0.2, 0.3, 0.4, -0.1] * 4
    result = load_module().horizon_health(
        [{"date": f"2026-01-{i+1:02d}", "value": value} for i, value in enumerate(values)],
        initial=20, stable=40,
    )
    assert result["status"] == "MIXED"
    assert result["observations"] == 20
    assert result["maturity_ratio"] == 1
    assert result["rank_ic_mean"] == pytest.approx(statistics.fmean(values))
    assert result["rank_ic_median"] == pytest.approx(statistics.median(values))
    assert result["rank_ic_std"] == pytest.approx(statistics.stdev(values))
    assert result["icir"] == pytest.approx(statistics.fmean(values) / statistics.stdev(values))
    assert result["positive_rate"] == pytest.approx(0.6)
    assert result["recent_5_count"] == 5 and result["recent_10_count"] == 10
    assert result["recent_5_mean"] == pytest.approx(statistics.fmean(values[-5:]))
    assert result["series"][-1]["value"] == -0.1


def test_zero_standard_deviation_has_null_icir():
    result = load_module().horizon_health(
        [{"date": f"2026-01-{i+1:02d}", "value": 0.1} for i in range(20)], 20, 40
    )
    assert result["rank_ic_std"] == 0
    assert result["icir"] is None


@pytest.mark.parametrize("recent,expected", [([0.02] * 5, "IMPROVING"), ([0.0] * 5, "WEAKENING"), ([0.014] * 5, "FLAT")])
def test_trend_compares_recent_five_with_prior_five(recent, expected):
    values = [0.01] * 15 + recent
    result = load_module().horizon_health(
        [{"date": f"2026-01-{i+1:02d}", "value": v} for i, v in enumerate(values)], 20, 40
    )
    assert result["trend"] == expected


def test_thresholds_and_governing_maturity_use_t5(fingerprint):
    learning = learning_with({"t1": [0.1] * 20, "t5": [0.1] * 19, "t20": [0.1] * 12}, fingerprint)
    payload = load_module().build_health_payload(learning, "2026-02-01T00:00:00Z")
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
    payload = load_module().build_health_payload(learning, "2026-02-01T00:00:00Z")
    assert payload["sample_maturity"]["status"] == "STABLE"
    assert payload["overall"]["status"] == "STABLE"
    assert 0 <= payload["overall"]["score"] <= 1


def test_full_payload_validates_and_cli_writes_atomically(tmp_path, fingerprint):
    learning = learning_with({"t1": [0.1] * 20, "t5": [0.1] * 20, "t20": [0.1] * 12}, fingerprint)
    learning_path, output = tmp_path / "learning.json", tmp_path / "health.json"
    learning_path.write_text(json.dumps(learning), encoding="utf-8")
    completed = subprocess.run([
        str(ROOT / ".build-venv/bin/python"), str(SCRIPT), "--learning", str(learning_path),
        "--output", str(output), "--generated-at", "2026-02-01T00:00:00Z",
    ], cwd=ROOT, text=True, capture_output=True, check=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    validator = load_module(VALIDATOR, "health_validator")
    assert validator.validate_us_compass_health_payload(payload) == []
    assert payload["generated_at"] == "2026-02-01T00:00:00Z"
    assert "health generated" in completed.stdout
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("learning,message", [
    ([], "learning"), ({"snapshots": []}, "snapshots"),
    ({"snapshots": [{"date": "2026-01-01", "outcomes": {}}]}, "model_fingerprint"),
    ({"snapshots": [{"date": "2026-01-01", "outcomes": {}}], "model_fingerprint": {}}, "model_fingerprint"),
])
def test_build_rejects_malformed_or_empty_learning(learning, message):
    with pytest.raises(ValueError, match=message):
        load_module().build_health_payload(learning, "2026-02-01T00:00:00Z")


def test_generated_payload_contains_no_nonfinite_numbers(fingerprint):
    payload = load_module().build_health_payload(
        learning_with({"t1": [0.1] * 20, "t5": [0.1] * 20, "t20": []}, fingerprint),
        "2026-02-01T00:00:00Z",
    )
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
        load_module().build_health_payload(learning, "2026-02-01")
