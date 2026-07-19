#!/usr/bin/env python3
"""Fail-closed public schema for the model-agnostic path snapshot."""
from __future__ import annotations

from typing import Any, Iterable

SENSITIVE_KEY_FRAGMENTS = (
    "api_key", "apikey", "secret", "credential", "password", "tokenizer",
    "checkpoint", "revision", "device", "database", "private",
)
SENSITIVE_EXACT_KEYS = {"path", "file_path", "local_path", "cache_path", "runtime_path"}

TOP_KEYS = (
    "schema_version", "mode", "model_family", "production_weights_changed",
    "formal_signal_logic_changed", "production_role", "generated_at",
    "latest_trade_date", "input_fingerprint", "data_basis",
    "forecast_definition", "model", "coverage", "summary", "validation", "items",
)
DATA_BASIS_KEYS = ("adjustment", "is_final", "universe", "expected_symbols")
FORECAST_KEYS = ("target", "horizon_sessions", "future_sessions", "interpretation")
MODEL_KEYS = ("parameters",)
PARAMETER_KEYS = ("lookback", "minimum_history", "horizon_sessions", "input_columns")
COVERAGE_KEYS = (
    "expected_symbols", "predicted_symbols", "failed_symbols",
    "raw_ohlc_valid_symbols", "minimum_history_bars", "median_history_bars",
)
SUMMARY_KEYS = ("bullish_symbols", "bearish_symbols", "median_predicted_return_pct", "mean_predicted_return_pct", "top_predicted")
TOP_PREDICTED_KEYS = ("symbol", "name", "predicted_return_pct")
VALIDATION_KEYS = ("status", "formal_promotion_eligible", "required_checks")
ITEM_KEYS = ("symbol", "name", "as_of", "history_bars", "close", "steps", "five_day", "quality")
STEP_KEYS = ("session", "date", "open", "high", "low", "close")
FIVE_DAY_KEYS = ("predicted_close", "predicted_return_pct", "path_high_pct", "path_low_pct")
QUALITY_KEYS = ("raw_ohlc_valid", "raw_errors", "input_columns")


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick(source: Any, keys: Iterable[str]) -> dict[str, Any]:
    source = _object(source)
    return {key: source.get(key) for key in keys}


def project_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Project any cache/generated payload onto the exact public contract."""
    data_basis = _object(payload.get("data_basis"))
    forecast = _object(payload.get("forecast_definition"))
    model = _object(payload.get("model"))
    parameters = _object(model.get("parameters"))
    coverage = _object(payload.get("coverage"))
    summary = _object(payload.get("summary"))
    validation = _object(payload.get("validation"))

    top_predicted = [
        _pick(item, TOP_PREDICTED_KEYS)
        for item in _array(summary.get("top_predicted"))
        if isinstance(item, dict)
    ]
    items = []
    for item in _array(payload.get("items")):
        if not isinstance(item, dict):
            continue
        projected = _pick(item, ITEM_KEYS)
        projected["steps"] = [
            _pick(step, STEP_KEYS)
            for step in _array(item.get("steps"))
            if isinstance(step, dict)
        ]
        projected["five_day"] = _pick(item.get("five_day"), FIVE_DAY_KEYS)
        projected["quality"] = _pick(item.get("quality"), QUALITY_KEYS)
        items.append(projected)

    return {
        "schema_version": payload.get("schema_version"),
        "mode": "shadow_research_only",
        "model_family": "sequence_path_model",
        "production_weights_changed": False,
        "formal_signal_logic_changed": False,
        "production_role": "display_and_audit_only",
        "generated_at": payload.get("generated_at"),
        "latest_trade_date": payload.get("latest_trade_date"),
        "input_fingerprint": payload.get("input_fingerprint"),
        "data_basis": {
            "adjustment": data_basis.get("adjustment"),
            "is_final": data_basis.get("is_final"),
            "universe": data_basis.get("universe"),
            "expected_symbols": data_basis.get("expected_symbols"),
        },
        "forecast_definition": {
            **_pick(forecast, FORECAST_KEYS),
            "interpretation": "deterministic research path; display only",
        },
        "model": {"parameters": _pick(parameters, PARAMETER_KEYS)},
        "coverage": _pick(coverage, COVERAGE_KEYS),
        "summary": {**_pick(summary, SUMMARY_KEYS), "top_predicted": top_predicted},
        "validation": _pick(validation, VALIDATION_KEYS),
        "items": items,
    }


def _exact_keys(errors: list[str], label: str, value: Any, expected: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    expected_keys = set(expected)
    if set(value) != expected_keys:
        errors.append(f"{label} keys must exactly match the public schema")
    return value


def _scan_sensitive(errors: list[str], value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_EXACT_KEYS or any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
                errors.append(f"{location}.{key} is a forbidden public key")
            _scan_sensitive(errors, child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_sensitive(errors, child, f"{location}[{index}]")
    elif isinstance(value, str) and ("<" in value or ">" in value):
        errors.append(f"{location} contains forbidden HTML delimiters")


def validate_public_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    root = _exact_keys(errors, "path-shadow", payload, TOP_KEYS)
    _scan_sensitive(errors, root)
    _exact_keys(errors, "path-shadow.data_basis", root.get("data_basis"), DATA_BASIS_KEYS)
    _exact_keys(errors, "path-shadow.forecast_definition", root.get("forecast_definition"), FORECAST_KEYS)
    model = _exact_keys(errors, "path-shadow.model", root.get("model"), MODEL_KEYS)
    _exact_keys(errors, "path-shadow.model.parameters", model.get("parameters"), PARAMETER_KEYS)
    _exact_keys(errors, "path-shadow.coverage", root.get("coverage"), COVERAGE_KEYS)
    summary = _exact_keys(errors, "path-shadow.summary", root.get("summary"), SUMMARY_KEYS)
    for index, item in enumerate(summary.get("top_predicted", []) if isinstance(summary.get("top_predicted"), list) else []):
        _exact_keys(errors, f"path-shadow.summary.top_predicted[{index}]", item, TOP_PREDICTED_KEYS)
    _exact_keys(errors, "path-shadow.validation", root.get("validation"), VALIDATION_KEYS)
    items = root.get("items")
    if not isinstance(items, list):
        errors.append("path-shadow.items must be an array")
        items = []
    for index, item_value in enumerate(items):
        item = _exact_keys(errors, f"path-shadow.items[{index}]", item_value, ITEM_KEYS)
        steps = item.get("steps")
        if not isinstance(steps, list):
            errors.append(f"path-shadow.items[{index}].steps must be an array")
            steps = []
        for step_index, step in enumerate(steps):
            _exact_keys(errors, f"path-shadow.items[{index}].steps[{step_index}]", step, STEP_KEYS)
        _exact_keys(errors, f"path-shadow.items[{index}].five_day", item.get("five_day"), FIVE_DAY_KEYS)
        _exact_keys(errors, f"path-shadow.items[{index}].quality", item.get("quality"), QUALITY_KEYS)
    if root.get("mode") != "shadow_research_only":
        errors.append("path-shadow mode must be shadow_research_only")
    if root.get("model_family") != "sequence_path_model":
        errors.append("path-shadow model_family must be model-agnostic")
    if root.get("production_weights_changed") is not False or root.get("formal_signal_logic_changed") is not False:
        errors.append("path-shadow production flags must remain false")
    if root.get("production_role") != "display_and_audit_only":
        errors.append("path-shadow production_role is invalid")
    return errors
