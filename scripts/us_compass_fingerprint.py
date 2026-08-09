"""Stable SHA256 fingerprints for US Compass model semantics."""
from __future__ import annotations

import hashlib
import json
import math
import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

UNKNOWN_MODEL_VERSION = "__MISSING_MODEL_VERSION__"
FINGERPRINT_FIELDS = (
    "model_version", "universe_count", "symbols_sha256", "config_sha256",
    "execution_basis", "one_way_cost", "initial_capital", "horizons",
    "exposure_mapping",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _finite_number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return 0.0 if number == 0.0 else number


def _required_string(value: Any, name: str, *, missing: str | None = None) -> str:
    if value is None and missing is not None:
        return missing
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonempty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a nonempty string")
    return normalized


def fingerprint_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_fingerprint_payload(
    *,
    model_version: str | None,
    symbols: Sequence[str],
    horizons: Sequence[int],
    one_way_cost: float,
    initial_capital: float,
    execution_basis: str,
    exposure_mapping: Mapping[str, float],
    default_exposure: float,
) -> dict[str, Any]:
    if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes)):
        raise ValueError("symbols must be a sequence")
    normalized_symbols: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbols must contain nonempty strings")
        normalized_symbols.add(symbol.strip().upper())
    if not normalized_symbols:
        raise ValueError("symbols must not be empty")

    if not isinstance(horizons, Sequence) or isinstance(horizons, (str, bytes)):
        raise ValueError("horizons must be unique positive integers")
    normalized_horizons = list(horizons)
    if (
        not normalized_horizons
        or any(isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0 for horizon in normalized_horizons)
        or len(set(normalized_horizons)) != len(normalized_horizons)
    ):
        raise ValueError("horizons must be unique positive integers")
    normalized_horizons.sort()

    cost = _finite_number(one_way_cost, "one_way_cost", minimum=0.0)
    capital = _finite_number(initial_capital, "initial_capital", minimum=0.0)
    if capital == 0.0:
        raise ValueError("initial_capital must be positive")

    if not isinstance(exposure_mapping, Mapping):
        raise ValueError("exposure_mapping must be an object")
    normalized_mapping: dict[str, float] = {}
    for key, value in exposure_mapping.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("exposure mapping key must be a nonempty string")
        normalized_key = key.strip()
        if normalized_key in normalized_mapping:
            raise ValueError("exposure mapping key normalization collision")
        normalized_mapping[normalized_key] = _finite_number(
            value, "exposure value", minimum=0.0, maximum=1.0
        )
    if not normalized_mapping:
        raise ValueError("exposure_mapping must not be empty")

    return {
        "model_version": _required_string(
            model_version, "model_version", missing=UNKNOWN_MODEL_VERSION
        ),
        "symbols": sorted(normalized_symbols),
        "horizons": normalized_horizons,
        "one_way_cost": cost,
        "initial_capital": capital,
        "execution_basis": _required_string(execution_basis, "execution_basis"),
        "exposure_mapping": {
            "values": normalized_mapping,
            "default": _finite_number(
                default_exposure, "default exposure", minimum=0.0, maximum=1.0
            ),
        },
    }


def build_model_fingerprint(
    pool: Mapping[str, Any],
    *,
    horizons: Sequence[int],
    one_way_cost: float,
    initial_capital: float,
    execution_basis: str,
    exposure_mapping: Mapping[str, float],
    default_exposure: float,
) -> dict[str, Any]:
    if not isinstance(pool, Mapping):
        raise ValueError("pool must be a mapping")
    rows = pool.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("pool rows must be a nonempty sequence")
    symbols: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("every pool rows item must be a mapping")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("every pool row symbol must be a nonempty string")
        symbols.append(symbol)

    model_version = pool.get("model_version")
    payload = canonical_fingerprint_payload(
        model_version=model_version,
        symbols=symbols,
        horizons=horizons,
        one_way_cost=one_way_cost,
        initial_capital=initial_capital,
        execution_basis=execution_basis,
        exposure_mapping=exposure_mapping,
        default_exposure=default_exposure,
    )
    normalized_symbols = payload["symbols"]
    return {
        "model_version": payload["model_version"],
        "universe_count": len(normalized_symbols),
        "symbols_sha256": fingerprint_sha256(normalized_symbols),
        "config_sha256": fingerprint_sha256(payload),
        "execution_basis": payload["execution_basis"],
        "one_way_cost": payload["one_way_cost"],
        "initial_capital": payload["initial_capital"],
        "horizons": payload["horizons"],
        "exposure_mapping": payload["exposure_mapping"],
    }


def _canonical_public_number(
    value: Any,
    name: str,
    *,
    positive: bool = False,
    maximum: float | None = None,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"model_fingerprint {name} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"model_fingerprint {name} is invalid") from exc
    if (
        not math.isfinite(number)
        or (number == 0.0 and math.copysign(1.0, number) < 0)
        or (number <= 0 if positive else number < 0)
        or (maximum is not None and number > maximum)
    ):
        raise ValueError(f"model_fingerprint {name} is invalid")
    return value


def validate_model_fingerprint(value: Any) -> dict[str, Any]:
    """Validate and project the canonical public model fingerprint fields."""
    if not isinstance(value, Mapping):
        raise ValueError("model_fingerprint must be an object")
    for field in FINGERPRINT_FIELDS:
        if field not in value:
            raise ValueError(f"model_fingerprint {field} is missing")

    model_version = value["model_version"]
    execution_basis = value["execution_basis"]
    for field, item in (("model_version", model_version), ("execution_basis", execution_basis)):
        if not isinstance(item, str) or not item or item != item.strip():
            raise ValueError(f"model_fingerprint {field} is invalid")

    universe_count = value["universe_count"]
    if isinstance(universe_count, bool) or not isinstance(universe_count, int) or universe_count < 1:
        raise ValueError("model_fingerprint universe_count is invalid")
    for field in ("symbols_sha256", "config_sha256"):
        digest = value[field]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"model_fingerprint {field} is invalid")

    _canonical_public_number(value["one_way_cost"], "one_way_cost")
    _canonical_public_number(value["initial_capital"], "initial_capital", positive=True)

    horizons = value["horizons"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in horizons)
        or horizons != sorted(set(horizons))
    ):
        raise ValueError("model_fingerprint horizons are invalid")

    exposure = value["exposure_mapping"]
    if not isinstance(exposure, Mapping) or set(exposure) != {"values", "default"}:
        raise ValueError("model_fingerprint exposure_mapping is invalid")
    values = exposure["values"]
    if not isinstance(values, Mapping) or not values:
        raise ValueError("model_fingerprint exposure_mapping is invalid")
    for key, number in values.items():
        if not isinstance(key, str) or not key or key != key.strip():
            raise ValueError("model_fingerprint exposure_mapping is invalid")
        _canonical_public_number(number, "exposure_mapping", maximum=1.0)
    _canonical_public_number(exposure["default"], "exposure_mapping", maximum=1.0)
    return {field: copy.deepcopy(value[field]) for field in FINGERPRINT_FIELDS}


def consistent_model_fingerprint(learning: Any, shadow: Any) -> dict[str, Any]:
    if not isinstance(learning, Mapping):
        raise ValueError("learning must be an object")
    if not isinstance(shadow, Mapping):
        raise ValueError("shadow must be an object")
    try:
        learning_fingerprint = validate_model_fingerprint(learning.get("model_fingerprint"))
    except ValueError as exc:
        raise ValueError(f"learning model_fingerprint is invalid: {exc}") from exc
    try:
        shadow_fingerprint = validate_model_fingerprint(shadow.get("model_fingerprint"))
    except ValueError as exc:
        raise ValueError(f"shadow model_fingerprint is invalid: {exc}") from exc
    if learning_fingerprint != shadow_fingerprint:
        raise ValueError("learning/shadow model fingerprint mismatch")
    return copy.deepcopy(learning_fingerprint)
