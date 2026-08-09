"""Stable SHA256 fingerprints for US Compass model semantics."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

UNKNOWN_MODEL_VERSION = "__MISSING_MODEL_VERSION__"


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
    except (TypeError, ValueError) as exc:
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
