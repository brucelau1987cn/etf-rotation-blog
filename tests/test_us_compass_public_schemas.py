from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_public_data_contracts.py"
SCHEMA_DIR = ROOT / "public" / "schemas"
SCHEMA_NAMES = (
    "us-compass-health.schema.json",
    "us-compass-rotation-map.schema.json",
    "us-compass-risk.schema.json",
)


def load_module():
    spec = importlib.util.spec_from_file_location("validate_public_data_contracts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator_module():
    return load_module()


@pytest.fixture
def model_fingerprint():
    return {
        "model_version": "us-compass-v1",
        "universe_count": 2,
        "symbols_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "execution_basis": "T close signal; T+1 open execution",
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "horizons": [1, 5, 20],
        "exposure_mapping": {
            "values": {"strong": 1.0, "neutral": 0.5, "defensive": 0.0},
            "default": 0.5,
        },
    }


@pytest.fixture
def health_payload(model_fingerprint):
    metric = {
        "status": "ACCUMULATING", "observations": 4, "minimum_required": 20,
        "maturity_ratio": 0.2, "rank_ic_mean": None, "rank_ic_median": None,
        "rank_ic_std": None, "icir": None, "positive_rate": None,
        "recent_5_mean": None, "recent_5_count": 0, "recent_10_mean": None,
        "recent_10_count": 0, "trend": None,
        "series": [
            {"signal_date": "2026-08-01", "date": "2026-08-02", "value": 0.1},
            {"signal_date": "2026-08-02", "date": "2026-08-03", "value": -0.1},
            {"signal_date": "2026-08-03", "date": "2026-08-04", "value": 0.2},
            {"signal_date": "2026-08-04", "date": "2026-08-05", "value": 0.0},
        ],
    }
    return {
        "schema_version": "us-compass-health-v1",
        "market": "US",
        "model_date": "2026-08-07",
        "generated_at": "2026-08-08T02:00:00Z",
        "model_fingerprint": model_fingerprint,
        "sample_maturity": {
            "status": "ACCUMULATING",
            "observations": 4,
            "minimum_observations": 20,
            "mature": False,
            "reasons": ["forward sample is immature"],
        },
        "horizons": {"t1": metric, "t5": metric, "t20": metric},
        "walk_forward": {"status": "ACCUMULATING", "windows": 0, "score": None},
        "shadow_health": {"status": "ACCUMULATING", "observations": 4, "return": None, "max_drawdown": None},
        "cost_sensitivity": {"status": "ACCUMULATING", "scenarios": [], "score": None},
        "overall": {"status": "ACCUMULATING", "score": None, "reasons": ["insufficient history"]},
    }


def set_health_result_statuses(payload, status):
    for horizon in payload["horizons"].values():
        horizon["status"] = status
    for section in ("walk_forward", "shadow_health", "cost_sensitivity", "overall"):
        payload[section]["status"] = status


@pytest.fixture
def rotation_payload():
    return {
        "schema_version": "us-compass-rotation-map-v1",
        "benchmark": "SPY",
        "long_window": 60,
        "short_window": 20,
        "trail_length": 3,
        "as_of": "2026-08-07",
        "items": [
            {
                "symbol": "QQQ",
                "theme": "Technology",
                "quadrant": "LEADING",
                "trail": [
                    {"date": "2026-08-05", "ratio": 1.01, "momentum": 0.02},
                    {"date": "2026-08-06", "ratio": 1.02, "momentum": 0.03},
                    {"date": "2026-08-07", "ratio": 1.03, "momentum": 0.04},
                ],
            }
        ],
    }


@pytest.fixture
def risk_payload():
    return {
        "schema_version": "us-compass-risk-v1",
        "as_of": "2026-08-07",
        "window": 60,
        "symbols": ["SPY", "QQQ"],
        "correlation_matrix": [[1.0, 0.75], [0.75, 1.0]],
        "volatility": {"status": "EVALUATED", "values": {"SPY": 0.12, "QQQ": 0.18}},
        "risk_contribution": {"status": "EVALUATED", "values": {"SPY": -0.1, "QQQ": 1.1}},
        "concentration": {"status": "EVALUATED", "score": 0.62, "largest_contributor": "QQQ"},
        "data_quality": {"status": "EVALUATED", "coverage": 1.0, "reasons": []},
    }


def test_schema_files_exist_and_are_valid_draft_2020_12():
    for name in SCHEMA_NAMES:
        path = SCHEMA_DIR / name
        assert path.exists(), name
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_valid_fixture_payloads_pass(validator_module, health_payload, rotation_payload, risk_payload):
    assert validator_module.validate_us_compass_health_payload(health_payload) == []
    assert validator_module.validate_us_compass_rotation_payload(rotation_payload) == []
    assert validator_module.validate_us_compass_risk_payload(risk_payload) == []


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda value: value["horizons"]["t1"].update(rank_ic_mean=0), "horizons.t1.rank_ic_mean"),
        (lambda value: value["horizons"]["t1"].update(rank_ic_median=0), "horizons.t1.rank_ic_median"),
        (lambda value: value["horizons"]["t1"].update(positive_rate=0), "horizons.t1.positive_rate"),
        (lambda value: value["walk_forward"].update(score=0), "walk_forward.score"),
        (lambda value: value["shadow_health"].update({"return": 0}), "shadow_health.return"),
        (
            lambda value: value["shadow_health"].update(max_drawdown=0),
            "shadow_health.max_drawdown",
        ),
        (lambda value: value["shadow_health"].update(score=0), "shadow_health.score"),
        (
            lambda value: value["cost_sensitivity"].update(
                scenarios=[{"one_way_cost": 0, "value": 0}]
            ),
            "cost_sensitivity.scenarios[0].value",
        ),
        (lambda value: value["cost_sensitivity"].update(score=0), "cost_sensitivity.score"),
        (lambda value: value["overall"].update(score=0), "overall.score"),
    ],
)
def test_immature_health_result_metrics_must_be_null(
    validator_module, health_payload, change, path
):
    change(health_payload)
    errors = validator_module.validate_us_compass_health_payload(health_payload)
    assert any(
        path in error and "must be null" in error
        for error in errors
    ), errors


@pytest.mark.parametrize(
    ("observations", "minimum_observations", "mature"),
    [(19, 20, True), (20, 20, False)],
)
def test_sample_mature_flag_must_match_observation_counts(
    validator_module, health_payload, observations, minimum_observations, mature
):
    health_payload["sample_maturity"].update(
        observations=observations,
        minimum_observations=minimum_observations,
        mature=mature,
    )

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any("sample_maturity.mature" in error and "must equal" in error for error in errors), errors


@pytest.mark.parametrize(
    ("observations", "minimum_observations", "mature", "status"),
    [
        (19, 20, False, "STABLE"),
        (20, 20, True, "ACCUMULATING"),
    ],
)
def test_sample_maturity_status_must_match_maturity_direction(
    validator_module,
    health_payload,
    observations,
    minimum_observations,
    mature,
    status,
):
    health_payload["sample_maturity"].update(
        observations=observations,
        minimum_observations=minimum_observations,
        mature=mature,
        status=status,
    )

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any("sample_maturity.status" in error and "inconsistent" in error for error in errors), errors


def test_mature_stable_health_accepts_legitimate_numeric_zero(validator_module, health_payload):
    health_payload["sample_maturity"].update(
        status="STABLE", observations=20, minimum_observations=20, mature=True
    )
    health_payload["horizons"]["t1"] = {
        "status": "STABLE",
        "observations": 20, "minimum_required": 20, "maturity_ratio": 1,
        "rank_ic_mean": 0, "rank_ic_median": 0, "rank_ic_std": 0,
        "icir": None, "positive_rate": 0, "recent_5_mean": 0,
        "recent_5_count": 5, "recent_10_mean": 0, "recent_10_count": 10,
        "trend": "FLAT",
        "series": [
            {"signal_date": f"2025-12-{index + 1:02d}", "date": f"2026-01-{index + 1:02d}", "value": 0}
            for index in range(20)
        ],
    }
    health_payload["walk_forward"] = {"status": "STABLE", "windows": 1, "score": 0}
    health_payload["shadow_health"] = {
        "status": "STABLE",
        "observations": 20,
        "return": 0,
        "max_drawdown": 0,
        "score": 0,
    }
    health_payload["cost_sensitivity"] = {
        "status": "STABLE",
        "scenarios": [{"one_way_cost": 0, "value": 0}],
        "score": 0,
    }
    health_payload["overall"] = {"status": "STABLE", "score": 0, "reasons": []}

    assert validator_module.validate_us_compass_health_payload(health_payload) == []


def test_mature_unavailable_health_accepts_null_results(validator_module, health_payload):
    health_payload["sample_maturity"].update(
        status="UNAVAILABLE", observations=20, minimum_observations=20, mature=True
    )
    set_health_result_statuses(health_payload, "UNAVAILABLE")

    assert validator_module.validate_us_compass_health_payload(health_payload) == []


@pytest.mark.parametrize(
    ("validator_name", "fixture_name", "mutate"),
    [
        ("validate_us_compass_health_payload", "health_payload", lambda value: value.pop("overall")),
        ("validate_us_compass_health_payload", "health_payload", lambda value: value["overall"].update(score="immature")),
        ("validate_us_compass_rotation_payload", "rotation_payload", lambda value: value.update(benchmark="")),
        ("validate_us_compass_rotation_payload", "rotation_payload", lambda value: value["items"][0].update(quadrant="HOT")),
        ("validate_us_compass_risk_payload", "risk_payload", lambda value: value.update(window=0)),
        ("validate_us_compass_risk_payload", "risk_payload", lambda value: value["data_quality"].pop("status")),
    ],
)
def test_malformed_payloads_fail_closed(
    request, validator_module, validator_name, fixture_name, mutate
):
    payload = copy.deepcopy(request.getfixturevalue(fixture_name))
    mutate(payload)
    assert getattr(validator_module, validator_name)(payload)


@pytest.mark.parametrize(
    "dates",
    [
        ["2026-08-05", "2026-08-05"],
        ["2026-08-06", "2026-08-05"],
    ],
)
def test_rotation_trail_dates_must_be_unique_and_ascending(
    validator_module, rotation_payload, dates
):
    rotation_payload["items"][0]["trail"] = [
        {"date": date, "ratio": 1.0, "momentum": 0.0} for date in dates
    ]
    errors = validator_module.validate_us_compass_rotation_payload(rotation_payload)
    assert any("trail" in error and ("ascending" in error or "unique" in error) for error in errors)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value.update(correlation_matrix=[[1.0, 0.5]]), "dimension"),
        (lambda value: value.update(correlation_matrix=[[1.0, 1.2], [1.2, 1.0]]), "maximum"),
        (lambda value: value["risk_contribution"]["values"].update(SPY=0.0), "sum"),
    ],
)
def test_risk_semantic_failures(validator_module, risk_payload, change, message):
    change(risk_payload)
    errors = validator_module.validate_us_compass_risk_payload(risk_payload)
    assert any(message in error for error in errors), errors


def test_negative_risk_contribution_is_allowed_when_total_is_one(validator_module, risk_payload):
    assert validator_module.validate_us_compass_risk_payload(risk_payload) == []


@pytest.mark.parametrize(
    ("validator_name", "fixture_name", "change", "message"),
    [
        (
            "validate_us_compass_health_payload",
            "health_payload",
            lambda value: value["overall"].update(note="<script>"),
            "HTML delimiter",
        ),
        (
            "validate_us_compass_rotation_payload",
            "rotation_payload",
            lambda value: value["items"][0].update(private_path="/root/private"),
            "forbidden public key",
        ),
        (
            "validate_us_compass_risk_payload",
            "risk_payload",
            lambda value: value["volatility"]["values"].update(SPY=float("nan")),
            "non-finite",
        ),
    ],
)
def test_public_payload_validators_reject_unsafe_content(
    request, validator_module, validator_name, fixture_name, change, message
):
    payload = request.getfixturevalue(fixture_name)
    change(payload)
    errors = getattr(validator_module, validator_name)(payload)
    assert any(message in error for error in errors), errors


def test_schema_registry_checks_new_schemas_without_future_data_files(validator_module, tmp_path):
    errors = []
    schemas = validator_module.validate_schema_files(SCHEMA_DIR, errors)
    assert not errors
    assert set(SCHEMA_NAMES).issubset(schemas)
    assert not list(tmp_path.iterdir())


def test_immature_health_series_length_and_outcome_date_order_are_enforced(
    validator_module, health_payload
):
    health_payload["horizons"]["t1"]["observations"] = 3
    errors = validator_module.validate_us_compass_health_payload(health_payload)
    assert any("length must equal observations" in error for error in errors), errors

    health_payload["horizons"]["t1"]["observations"] = 4
    health_payload["horizons"]["t1"]["series"][1]["date"] = "2026-08-02"
    errors = validator_module.validate_us_compass_health_payload(health_payload)
    assert any("unique and strictly ascending" in error for error in errors), errors


def test_health_series_rejects_duplicate_signal_dates(validator_module, health_payload):
    health_payload["horizons"]["t1"]["series"][1]["signal_date"] = "2026-08-01"

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any(
        "signal dates must be unique and strictly ascending" in error for error in errors
    ), errors


def test_health_series_rejects_out_of_order_signal_dates(validator_module, health_payload):
    health_payload["horizons"]["t1"]["series"][1]["signal_date"] = "2026-07-31"

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any(
        "signal dates must be unique and strictly ascending" in error for error in errors
    ), errors


def test_health_series_rejects_signal_date_on_or_after_outcome_date(
    validator_module, health_payload
):
    health_payload["horizons"]["t1"]["series"][0]["signal_date"] = "2026-08-02"

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any("signal_date must be before date" in error for error in errors), errors


@pytest.mark.parametrize("field", ["signal_date", "date"])
def test_health_series_requires_valid_signal_and_outcome_dates(
    validator_module, health_payload, field
):
    health_payload["horizons"]["t1"]["series"][0][field] = "2026-02-30"

    errors = validator_module.validate_us_compass_health_payload(health_payload)

    assert any("valid YYYY-MM-DD format" in error for error in errors), errors


def test_health_series_accepts_valid_signal_and_outcome_date_sequences(
    validator_module, health_payload
):
    assert validator_module.validate_us_compass_health_payload(health_payload) == []
