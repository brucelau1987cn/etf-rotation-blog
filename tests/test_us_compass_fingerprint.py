from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "us_compass_fingerprint.py"


def load_module():
    spec = importlib.util.spec_from_file_location("us_compass_fingerprint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fingerprint_module_exists():
    assert SCRIPT.exists()


def test_build_model_fingerprint_canonicalizes_semantic_inputs():
    module = load_module()
    kwargs = {
        "horizons": (1, 5, 20),
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "execution_basis": "T close signal; T+1 open execution; next-open rebalance",
        "exposure_mapping": {"偏强": 1.0, "震荡": 0.5, "防御": 0.0},
        "default_exposure": 0.5,
    }
    first = module.build_model_fingerprint(
        {
            "generated_at": "2026-08-01T00:00:00Z",
            "model_version": "v1",
            "rows": [{"symbol": "spy"}, {"symbol": "QQQ"}, {"symbol": "SPY"}],
        },
        **kwargs,
    )
    second = module.build_model_fingerprint(
        {
            "generated_at": "2026-08-02T00:00:00Z",
            "rows": [{"symbol": "SPY"}, {"symbol": "QQQ"}],
            "model_version": "v1",
        },
        **kwargs,
    )

    assert first == second
    assert first["model_version"] == "v1"
    assert first["universe_count"] == 2
    assert len(first["symbols_sha256"]) == 64
    assert len(first["config_sha256"]) == 64
    assert first["horizons"] == [1, 5, 20]
    assert first["exposure_mapping"] == {
        "values": {"偏强": 1.0, "震荡": 0.5, "防御": 0.0},
        "default": 0.5,
    }


def test_missing_model_version_uses_stable_unknown_not_timestamp():
    module = load_module()
    kwargs = {
        "horizons": (1, 5, 20),
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "execution_basis": "basis",
        "exposure_mapping": {"偏强": 1.0},
        "default_exposure": 0.5,
    }
    first = module.build_model_fingerprint(
        {"generated_at": "2026-01-01", "rows": [{"symbol": "SPY"}]}, **kwargs
    )
    second = module.build_model_fingerprint(
        {"generated_at": "2027-01-01", "rows": [{"symbol": "SPY"}]}, **kwargs
    )
    assert first == second
    assert first["model_version"] == "__MISSING_MODEL_VERSION__"


def test_horizon_order_does_not_change_fingerprint():
    module = load_module()
    kwargs = {"one_way_cost": 0.001, "initial_capital": 20_000.0,
              "execution_basis": "basis", "exposure_mapping": {"偏强": 1.0},
              "default_exposure": 0.5}
    pool = {"model_version": "v1", "rows": [{"symbol": "SPY"}]}
    first = module.build_model_fingerprint(pool, horizons=(20, 1, 5), **kwargs)
    second = module.build_model_fingerprint(pool, horizons=(5, 20, 1), **kwargs)
    assert first == second
    assert first["horizons"] == [1, 5, 20]


@pytest.mark.parametrize("field", ["one_way_cost", "initial_capital", "default_exposure"])
def test_boolean_numbers_are_rejected(field):
    module = load_module()
    inputs = {"model_version": "v1", "symbols": ["SPY"], "horizons": (1,),
              "one_way_cost": 0.0, "initial_capital": 1.0, "execution_basis": "basis",
              "exposure_mapping": {"on": 1.0}, "default_exposure": 0.0}
    inputs[field] = True
    with pytest.raises(ValueError):
        module.canonical_fingerprint_payload(**inputs)


def test_boolean_exposure_value_is_rejected():
    module = load_module()
    with pytest.raises(ValueError, match="exposure"):
        module.canonical_fingerprint_payload(
            model_version="v1", symbols=["SPY"], horizons=(1,), one_way_cost=0,
            initial_capital=1, execution_basis="basis", exposure_mapping={"on": True},
            default_exposure=0)


def test_allowed_signed_zero_is_normalized():
    module = load_module()
    payload = module.canonical_fingerprint_payload(
        model_version="v1", symbols=["SPY"], horizons=(1,), one_way_cost=-0.0,
        initial_capital=1, execution_basis="basis", exposure_mapping={"off": -0.0},
        default_exposure=-0.0)
    assert payload["one_way_cost"] == 0.0
    assert payload["exposure_mapping"] == {"values": {"off": 0.0}, "default": 0.0}
    assert "-0.0" not in str(payload)


@pytest.mark.parametrize("bad_key", [1, None, True, "   "])
def test_exposure_keys_must_be_nonempty_strings(bad_key):
    module = load_module()
    with pytest.raises(ValueError, match="exposure.*key"):
        module.canonical_fingerprint_payload(
            model_version="v1", symbols=["SPY"], horizons=(1,), one_way_cost=0,
            initial_capital=1, execution_basis="basis", exposure_mapping={bad_key: 0.5},
            default_exposure=0.5)


def test_exposure_key_normalization_collision_is_rejected():
    module = load_module()
    with pytest.raises(ValueError, match="collision"):
        module.canonical_fingerprint_payload(
            model_version="v1", symbols=["SPY"], horizons=(1,), one_way_cost=0,
            initial_capital=1, execution_basis="basis",
            exposure_mapping={"A": 1.0, " A ": 0.5}, default_exposure=0.5)


@pytest.mark.parametrize("field,value", [
    ("model_version", "  "), ("model_version", 1),
    ("execution_basis", "  "), ("execution_basis", None), ("execution_basis", 1),
])
def test_required_strings_are_stripped_nonempty_strings(field, value):
    module = load_module()
    inputs = {"model_version": " v1 ", "symbols": ["SPY"], "horizons": (1,),
              "one_way_cost": 0, "initial_capital": 1, "execution_basis": " basis ",
              "exposure_mapping": {" on ": 1}, "default_exposure": 0}
    inputs[field] = value
    with pytest.raises(ValueError, match=field):
        module.canonical_fingerprint_payload(**inputs)


def test_required_strings_and_exposure_keys_are_stripped():
    module = load_module()
    payload = module.canonical_fingerprint_payload(
        model_version=" v1 ", symbols=["SPY"], horizons=(1,), one_way_cost=0,
        initial_capital=1, execution_basis=" basis ", exposure_mapping={" on ": 1},
        default_exposure=0)
    assert payload["model_version"] == "v1"
    assert payload["execution_basis"] == "basis"
    assert payload["exposure_mapping"]["values"] == {"on": 1.0}


@pytest.mark.parametrize("pool", [None, [], "pool"])
def test_build_rejects_non_mapping_pool(pool):
    module = load_module()
    with pytest.raises(ValueError, match="pool"):
        module.build_model_fingerprint(pool, horizons=(1,), one_way_cost=0,
            initial_capital=1, execution_basis="basis", exposure_mapping={"on": 1},
            default_exposure=0)


@pytest.mark.parametrize("rows", [None, [], {}, "SPY", [None], [{"symbol": None}], [{"symbol": 1}], [{"symbol": "  "}]])
def test_build_rejects_malformed_rows_without_partial_universe(rows):
    module = load_module()
    with pytest.raises(ValueError, match="rows|symbol"):
        module.build_model_fingerprint({"model_version": "v1", "rows": rows},
            horizons=(1,), one_way_cost=0, initial_capital=1, execution_basis="basis",
            exposure_mapping={"on": 1}, default_exposure=0)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("symbols", ["SPY", "QQQ"]),
        ("model_version", "v2"),
        ("horizons", (1, 10, 20)),
        ("one_way_cost", 0.002),
        ("initial_capital", 30_000.0),
        ("execution_basis", "different basis"),
        ("exposure_mapping", {"偏强": 0.9, "震荡": 0.5, "防御": 0.0}),
        ("default_exposure", 0.4),
    ],
)
def test_each_semantic_input_changes_config_sha256(field, changed):
    module = load_module()
    inputs = {
        "model_version": "v1",
        "symbols": ["SPY"],
        "horizons": (1, 5, 20),
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "execution_basis": "basis",
        "exposure_mapping": {"偏强": 1.0, "震荡": 0.5, "防御": 0.0},
        "default_exposure": 0.5,
    }
    baseline = module.fingerprint_sha256(module.canonical_fingerprint_payload(**inputs))
    inputs[field] = changed
    modified = module.fingerprint_sha256(module.canonical_fingerprint_payload(**inputs))
    assert modified != baseline


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"symbols": []}, "symbols"),
        ({"one_way_cost": float("nan")}, "one_way_cost"),
        ({"one_way_cost": float("inf")}, "one_way_cost"),
        ({"initial_capital": float("-inf")}, "initial_capital"),
        ({"horizons": (0, 5, 20)}, "horizons"),
        ({"horizons": (1, 1, 20)}, "horizons"),
        ({"exposure_mapping": {"偏强": 1.1}}, "exposure"),
        ({"default_exposure": -0.1}, "exposure"),
    ],
)
def test_invalid_semantic_inputs_are_rejected(override, message):
    module = load_module()
    inputs = {
        "model_version": "v1",
        "symbols": ["SPY"],
        "horizons": (1, 5, 20),
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "execution_basis": "basis",
        "exposure_mapping": {"偏强": 1.0, "震荡": 0.5, "防御": 0.0},
        "default_exposure": 0.5,
    }
    inputs.update(override)
    with pytest.raises(ValueError, match=message):
        module.canonical_fingerprint_payload(**inputs)
