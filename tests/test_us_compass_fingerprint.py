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
    assert first["model_version"] == "UNKNOWN"


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
