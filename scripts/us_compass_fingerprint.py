"""Stable SHA256 fingerprints for US Compass model semantics."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

UNKNOWN_MODEL_VERSION = "UNKNOWN"


def _finite_number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
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
    return number


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
    model_version: str,
    symbols: Sequence[str],
    horizons: Sequence[int],
    one_way_cost: float,
    initial_capital: float,
    execution_basis: str,
    exposure_mapping: Mapping[str, float],
    default_exposure: float,
) -> dict[str, Any]:
    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not normalized_symbols:
        raise ValueError("symbols must not be empty")
    normalized_horizons = list(horizons)
    if (
        not normalized_horizons
        or any(isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0 for horizon in normalized_horizons)
        or len(set(normalized_horizons)) != len(normalized_horizons)
    ):
        raise ValueError("horizons must be unique positive integers")
    cost = _finite_number(one_way_cost, "one_way_cost", minimum=0.0)
    capital = _finite_number(initial_capital, "initial_capital", minimum=0.0)
    if capital == 0:
        raise ValueError("initial_capital must be positive")
    normalized_mapping = {
        str(key): _finite_number(value, "exposure value", minimum=0.0, maximum=1.0)
        for key, value in exposure_mapping.items()
    }
    if not normalized_mapping:
        raise ValueError("exposure_mapping must not be empty")
    normalized_default = _finite_number(default_exposure, "default exposure", minimum=0.0, maximum=1.0)
    return {
        "model_version": str(model_version or UNKNOWN_MODEL_VERSION),
        "symbols": normalized_symbols,
        "horizons": normalized_horizons,
        "one_way_cost": cost,
        "initial_capital": capital,
        "execution_basis": str(execution_basis),
        "exposure_mapping": {
            "values": normalized_mapping,
            "default": normalized_default,
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
    payload = canonical_fingerprint_payload(
        model_version=str(pool.get("model_version") or UNKNOWN_MODEL_VERSION),
        symbols=[str(row.get("symbol") or "") for row in pool.get("rows", []) if isinstance(row, Mapping)],
        horizons=horizons,
        one_way_cost=one_way_cost,
        initial_capital=initial_capital,
        execution_basis=execution_basis,
        exposure_mapping=exposure_mapping,
        default_exposure=default_exposure,
    )
    symbols = payload["symbols"]
    return {
        "model_version": payload["model_version"],
        "universe_count": len(symbols),
        "symbols_sha256": fingerprint_sha256(symbols),
        "config_sha256": fingerprint_sha256(payload),
        "execution_basis": payload["execution_basis"],
        "one_way_cost": payload["one_way_cost"],
        "initial_capital": payload["initial_capital"],
        "horizons": payload["horizons"],
        "exposure_mapping": payload["exposure_mapping"],
    }
