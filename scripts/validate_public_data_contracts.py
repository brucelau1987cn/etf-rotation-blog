#!/usr/bin/env python3
"""Fail-closed validation for public schemas, catalog metadata and dashboard payloads."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:
    from generate_data_catalog import DATASETS, active_dataset_specs, entry_for, stable_batch_id
    from generate_public_dashboard_payloads import A_FIELDS, build_payload as build_dashboard_payload, dashboard_batch_id
except ModuleNotFoundError:
    from scripts.generate_data_catalog import DATASETS, active_dataset_specs, entry_for, stable_batch_id
    from scripts.generate_public_dashboard_payloads import A_FIELDS, build_payload as build_dashboard_payload, dashboard_batch_id

if __package__:
    from .us_compass_research_metrics import rate_shadow_health, rate_time_slice_audit
else:
    metrics_path = Path(__file__).resolve().with_name("us_compass_research_metrics.py")
    metrics_spec = importlib.util.spec_from_file_location(
        f"_us_compass_research_metrics_{id(metrics_path)}", metrics_path
    )
    if metrics_spec is None or metrics_spec.loader is None:
        raise ImportError(f"cannot load research metrics from {metrics_path}")
    metrics_module = importlib.util.module_from_spec(metrics_spec)
    metrics_spec.loader.exec_module(metrics_module)
    rate_time_slice_audit = metrics_module.rate_time_slice_audit
    rate_shadow_health = metrics_module.rate_shadow_health

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
SCHEMAS = ROOT / "public/schemas"
SCHEMA_FILES = (
    "data-catalog.schema.json", "a-compass-dashboard.schema.json",
    "forward-evidence-ledger.schema.json", "decision-thesis.schema.json", "decision-drift.schema.json",
    "investment-research-layer.schema.json",
    "us-compass-health.schema.json", "us-compass-rotation-map.schema.json", "us-compass-risk.schema.json",
)
ROLES = {"production", "shadow", "history", "runtime", "export"}
SOURCE_CATEGORIES = {
    "market_data", "historical_market_data", "official_statistics", "public_events",
    "derived_research", "model_output", "simulated_execution", "publication_receipt",
}
FORBIDDEN_KEYS = re.compile(
    r"(?:api[_-]?key|access[_-]?key|token|secret|password|credential|checkpoint|tokenizer|"
    r"private[_-]?path|model[_-]?path|db[_-]?path|database[_-]?path|revision|device)",
    re.I,
)
PRIVATE_PATH = re.compile(r"(?:/root/|/home/|/Users/|[A-Za-z]:\\|file://)")
HTML_DELIMITER = re.compile(r"[<>]")
HORIZON_THRESHOLDS = {"t5": (20, 40)}


class ValidationResult(NamedTuple):
    status: str
    errors: list[str]


def parse_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing file: {path}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def unsafe_paths(value: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if FORBIDDEN_KEYS.search(str(key)):
                errors.append(f"{child_path}: forbidden public key")
            errors.extend(unsafe_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(unsafe_paths(child, f"{path}[{index}]"))
    elif isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path}: non-finite number")
    elif isinstance(value, str):
        if PRIVATE_PATH.search(value):
            errors.append(f"{path}: private path is forbidden")
        if HTML_DELIMITER.search(value):
            errors.append(f"{path}: HTML delimiter is forbidden")
    return errors


def validate_schema_files(schema_dir: Path, errors: list[str]) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for name in SCHEMA_FILES:
        path = schema_dir / name
        try:
            schema = parse_json(path)
            if not isinstance(schema, dict):
                raise ValueError("schema root must be an object")
            Draft202012Validator.check_schema(schema)
            schemas[name] = schema
            errors.extend(f"schema {name} {message}" for message in unsafe_paths(schema))
        except (ValueError, SchemaError) as exc:
            errors.append(f"schema {name}: {exc}")
    return schemas


def schema_errors(schema: dict[str, Any], payload: Any) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{'.'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}" for item in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))]


def _schema(name: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    return schema if schema is not None else parse_json(SCHEMAS / name)


def _require_null_immature_metrics(
    section: Any,
    fields: tuple[str, ...],
    path: str,
    sample_immature: bool = False,
) -> list[str]:
    if not isinstance(section, dict):
        return []
    status = section.get("status")
    if not sample_immature and status not in {"ACCUMULATING", "UNAVAILABLE"}:
        return []
    reason = (
        "sample maturity is immature"
        if sample_immature
        else f"status is {status}"
    )
    return [
        f"{path}.{field}: must be null while {reason}"
        for field in fields
        if field in section and section[field] is not None
    ]


def validate_us_compass_health_payload(
    payload: Any, schema: dict[str, Any] | None = None
) -> list[str]:
    errors = schema_errors(_schema("us-compass-health.schema.json", schema), payload) + unsafe_paths(payload)
    if not isinstance(payload, dict):
        return errors

    sample_maturity = payload.get("sample_maturity")
    sample_immature = False
    if isinstance(sample_maturity, dict):
        observations = sample_maturity.get("observations")
        minimum_observations = sample_maturity.get("minimum_observations")
        mature = sample_maturity.get("mature")
        status = sample_maturity.get("status")
        if (
            isinstance(observations, int)
            and not isinstance(observations, bool)
            and isinstance(minimum_observations, int)
            and not isinstance(minimum_observations, bool)
            and isinstance(mature, bool)
        ):
            if minimum_observations != HORIZON_THRESHOLDS["t5"][0]:
                errors.append("sample_maturity.minimum_observations: must equal 20")
            sample_immature = not mature

    horizons = payload.get("horizons")
    if isinstance(horizons, dict):
        for name, horizon in horizons.items():
            expected_immature = False
            if isinstance(horizon, dict):
                observations = horizon.get("observations")
                minimum_required = horizon.get("minimum_required")
                maturity_ratio = horizon.get("maturity_ratio")
                if (
                    isinstance(observations, int)
                    and not isinstance(observations, bool)
                    and isinstance(minimum_required, int)
                    and not isinstance(minimum_required, bool)
                    and minimum_required > 0
                ):
                    expected_ratio = min(1.0, observations / minimum_required)
                    if not isinstance(maturity_ratio, (int, float)) or isinstance(maturity_ratio, bool) or not math.isfinite(maturity_ratio) or abs(maturity_ratio - expected_ratio) > 1e-12:
                        errors.append(f"horizons.{name}.maturity_ratio: inconsistent with observations")
                    expected_immature = observations < minimum_required
                    status = horizon.get("status")
                    if expected_immature and status not in {"ACCUMULATING", "UNAVAILABLE"}:
                        errors.append(f"horizons.{name}.status: must be ACCUMULATING or UNAVAILABLE before its sample threshold")
                    if not expected_immature and status == "ACCUMULATING":
                        errors.append(f"horizons.{name}.status: cannot be ACCUMULATING after its sample threshold")
                else:
                    expected_immature = False
            errors.extend(
                _require_null_immature_metrics(
                    horizon,
                    (
                        "rank_ic_mean", "rank_ic_median", "rank_ic_std", "icir",
                        "positive_rate", "recent_5_mean", "recent_10_mean", "trend",
                    ),
                    f"horizons.{name}",
                    expected_immature,
                )
            )
            if isinstance(horizon, dict) and expected_immature:
                if horizon.get("recent_5_count") != 0 or horizon.get("recent_10_count") != 0:
                    errors.append(f"horizons.{name}: recent counts must be zero while horizon is immature")
            if isinstance(horizon, dict):
                series = horizon.get("series")
                observations = horizon.get("observations")
                if isinstance(series, list) and isinstance(observations, int) and len(series) != observations:
                    errors.append(f"horizons.{name}.series: length must equal observations")
                dates: list[str] = [
                    point["date"]
                    for point in series
                    if isinstance(point, dict) and isinstance(point.get("date"), str)
                ] if isinstance(series, list) else []
                signal_dates: list[str] = [
                    point["signal_date"]
                    for point in series
                    if isinstance(point, dict) and isinstance(point.get("signal_date"), str)
                ] if isinstance(series, list) else []
                dates_valid = isinstance(series, list) and len(dates) == len(series) and all(valid_date(date) for date in dates)
                signal_dates_valid = isinstance(series, list) and len(signal_dates) == len(series) and all(valid_date(date) for date in signal_dates)
                if isinstance(series, list) and not dates_valid:
                    errors.append(f"horizons.{name}.series: outcome dates must use valid YYYY-MM-DD format")
                if dates_valid and (dates != sorted(dates) or len(dates) != len(set(dates))):
                    errors.append(f"horizons.{name}.series: dates must be unique and strictly ascending")
                if isinstance(series, list) and not signal_dates_valid:
                    errors.append(f"horizons.{name}.series: signal dates must use valid YYYY-MM-DD format")
                if signal_dates_valid and (
                    signal_dates != sorted(signal_dates)
                    or len(signal_dates) != len(set(signal_dates))
                ):
                    errors.append(f"horizons.{name}.series: signal dates must be unique and strictly ascending")
                if dates_valid and signal_dates_valid:
                    for index, (signal_date, outcome_date) in enumerate(zip(signal_dates, dates)):
                        if signal_date >= outcome_date:
                            errors.append(
                                f"horizons.{name}.series[{index}].signal_date must be before date"
                            )

    walk_forward = payload.get("walk_forward")
    t5 = horizons.get("t5") if isinstance(horizons, dict) else None
    if isinstance(sample_maturity, dict) and isinstance(t5, dict):
        sample_observations = sample_maturity.get("observations")
        t5_observations = t5.get("observations")
        mature = sample_maturity.get("mature")
        status = sample_maturity.get("status")
        if (
            isinstance(sample_observations, int)
            and not isinstance(sample_observations, bool)
            and isinstance(t5_observations, int)
            and not isinstance(t5_observations, bool)
        ):
            if sample_observations != t5_observations:
                errors.append(
                    "sample_maturity.observations: must equal horizons.t5.observations"
                )
            expected_mature = t5_observations >= HORIZON_THRESHOLDS["t5"][0]
            if mature != expected_mature:
                errors.append(
                    "sample_maturity.mature: must equal horizons.t5.observations >= 20"
                )
            allowed_statuses = (
                {"FRAGILE", "MIXED", "STABLE", "UNAVAILABLE"}
                if expected_mature
                else {"ACCUMULATING", "UNAVAILABLE"}
            )
            if status not in allowed_statuses:
                errors.append(
                    "sample_maturity.status: inconsistent with observation maturity"
                )
            sample_immature = not expected_mature or mature is not True
    if isinstance(walk_forward, dict):
        slices = walk_forward.get("slices")
        if isinstance(slices, list):
            if walk_forward.get("windows") != len(slices):
                errors.append("walk_forward.windows: must equal slices length")
            evaluated = 0
            positive = 0
            total_observations = 0
            previous_end = None
            previous_signal_end = None
            for index, item in enumerate(slices):
                if not isinstance(item, dict):
                    continue
                if item.get("index") != index:
                    errors.append(f"walk_forward.slices[{index}].index: must be sequential")
                observations = item.get("observations")
                status = item.get("status")
                mean = item.get("mean")
                positive_rate = item.get("positive_rate")
                if isinstance(observations, int) and not isinstance(observations, bool):
                    total_observations += observations
                    if index < len(slices) - 1 and observations != 5:
                        errors.append(
                            f"walk_forward.slices[{index}]: partial INSUFFICIENT slice may only be final"
                        )
                    if observations < 5:
                        if status != "INSUFFICIENT" or mean is not None or positive_rate is not None:
                            errors.append(
                                f"walk_forward.slices[{index}]: INSUFFICIENT requires fewer than 5 observations and null metrics"
                            )
                    elif observations == 5:
                        evaluated += 1
                        if status == "POSITIVE":
                            positive += 1
                        if status not in {"POSITIVE", "NON_POSITIVE"} or mean is None or positive_rate is None:
                            errors.append(
                                f"walk_forward.slices[{index}]: evaluated slice requires status and non-null metrics"
                            )
                        elif (status == "POSITIVE") != (mean > 0):
                            errors.append(
                                f"walk_forward.slices[{index}].status: must match mean direction"
                            )
                start_date = item.get("start_date")
                end_date = item.get("end_date")
                signal_start = item.get("signal_start_date")
                signal_end = item.get("signal_end_date")
                if isinstance(start_date, str) and isinstance(end_date, str) and all(valid_date(value) for value in (start_date, end_date)):
                    if start_date > end_date or (previous_end is not None and start_date <= previous_end):
                        errors.append("walk_forward.slices: outcome date ranges must be strictly ascending and non-overlapping")
                    previous_end = end_date
                if isinstance(signal_start, str) and isinstance(signal_end, str) and all(valid_date(value) for value in (signal_start, signal_end)):
                    if signal_start > signal_end or (
                        previous_signal_end is not None and signal_start <= previous_signal_end
                    ):
                        errors.append("walk_forward.slices: signal date ranges must be strictly ascending and non-overlapping")
                    previous_signal_end = signal_end
            if walk_forward.get("evaluated_windows") != evaluated:
                errors.append("walk_forward.evaluated_windows: inconsistent with slices")
            if walk_forward.get("positive_windows") != positive:
                errors.append("walk_forward.positive_windows: inconsistent with slices")
            expected_rate = positive / evaluated if evaluated else None
            actual_rate = walk_forward.get("positive_slice_rate")
            if expected_rate is None:
                if actual_rate is not None:
                    errors.append("walk_forward.positive_slice_rate: must be null without evaluated slices")
            elif not isinstance(actual_rate, (int, float)) or isinstance(actual_rate, bool) or abs(actual_rate - expected_rate) > 1e-12:
                errors.append("walk_forward.positive_slice_rate: inconsistent with slices")
            if isinstance(t5, dict):
                observations = t5.get("observations")
                if isinstance(observations, int) and total_observations != observations:
                    errors.append("walk_forward.slices observations: sum must equal horizons.t5.observations")
                series = t5.get("series")
                if isinstance(series, list):
                    for index, item in enumerate(slices):
                        if not isinstance(item, dict):
                            continue
                        chunk = series[index * 5 : index * 5 + 5]
                        if not chunk:
                            continue
                        expected_boundaries = (
                            chunk[0].get("date"), chunk[-1].get("date"),
                            chunk[0].get("signal_date"), chunk[-1].get("signal_date"),
                        ) if all(isinstance(point, dict) for point in chunk) else None
                        actual_boundaries = (
                            item.get("start_date"), item.get("end_date"),
                            item.get("signal_start_date"), item.get("signal_end_date"),
                        )
                        if expected_boundaries is not None and actual_boundaries != expected_boundaries:
                            errors.append(f"walk_forward.slices[{index}]: boundaries must match consecutive T+5 observations")
                        if item.get("observations") != len(chunk):
                            errors.append(f"walk_forward.slices[{index}].observations: must match T+5 assignment")
                        if len(chunk) == 5 and all(
                            isinstance(point.get("value"), (int, float))
                            and not isinstance(point.get("value"), bool)
                            for point in chunk if isinstance(point, dict)
                        ):
                            values = [point["value"] for point in chunk]
                            expected_mean = sum(values) / 5
                            expected_positive_rate = sum(value > 0 for value in values) / 5
                            if not isinstance(item.get("mean"), (int, float)) or abs(item["mean"] - expected_mean) > 1e-12:
                                errors.append(f"walk_forward.slices[{index}].mean: inconsistent with T+5 observations")
                            if not isinstance(item.get("positive_rate"), (int, float)) or abs(item["positive_rate"] - expected_positive_rate) > 1e-12:
                                errors.append(f"walk_forward.slices[{index}].positive_rate: inconsistent with T+5 observations")
        if (
            isinstance(sample_maturity, dict)
            and sample_maturity.get("mature") is True
            and sample_maturity.get("status") != "UNAVAILABLE"
        ):
            rate = walk_forward.get("positive_slice_rate")
            icir = t5.get("icir") if isinstance(t5, dict) else None
            expected_status = None
            observations = t5.get("observations") if isinstance(t5, dict) else None
            if (
                isinstance(observations, int)
                and not isinstance(observations, bool)
                and isinstance(rate, (int, float))
                and not isinstance(rate, bool)
                and math.isfinite(rate)
                and (
                    icir is None
                    or isinstance(icir, (int, float))
                    and not isinstance(icir, bool)
                    and math.isfinite(icir)
                )
            ):
                expected_status = rate_time_slice_audit(
                    observations,
                    rate,
                    icir,
                    HORIZON_THRESHOLDS["t5"][0],
                )
            if expected_status is not None and walk_forward.get("status") != expected_status:
                errors.append("walk_forward.status: inconsistent with time-slice rating")
            if walk_forward.get("score") != walk_forward.get("positive_slice_rate"):
                errors.append("walk_forward.score: must equal positive_slice_rate after maturity")
            overall = payload.get("overall")
            if isinstance(overall, dict):
                if overall.get("status") != walk_forward.get("status"):
                    errors.append("overall.status: must equal walk_forward.status after maturity")
                if overall.get("score") != walk_forward.get("score"):
                    errors.append("overall.score: must equal walk_forward.score after maturity")
            if sample_maturity.get("status") != walk_forward.get("status"):
                errors.append("sample_maturity.status: must equal walk_forward.status after maturity")
    errors.extend(
        _require_null_immature_metrics(
            payload.get("walk_forward"), ("score",), "walk_forward", sample_immature
        )
    )
    shadow_health = payload.get("shadow_health")
    if isinstance(shadow_health, dict):
        observations = shadow_health.get("observations")
        initial = shadow_health.get("initial_capital")
        portfolios = shadow_health.get("portfolios")
        if isinstance(portfolios, dict):
            fusion = portfolios.get("fusion")
            for name, portfolio in portfolios.items():
                if not isinstance(portfolio, dict):
                    continue
                series = portfolio.get("equity_series")
                count = portfolio.get("observations")
                if isinstance(series, list) and isinstance(count, int) and len(series) != count:
                    errors.append(f"shadow_health.portfolios.{name}.equity_series: length must equal observations")
                dates = [point.get("date") for point in series if isinstance(point, dict)] if isinstance(series, list) else []
                if isinstance(series, list) and (
                    len(dates) != len(series)
                    or not all(isinstance(value, str) and valid_date(value) for value in dates)
                ):
                    errors.append(f"shadow_health.portfolios.{name}.equity_series: dates must be valid")
                elif dates != sorted(dates) or len(dates) != len(set(dates)):
                    errors.append(f"shadow_health.portfolios.{name}.equity_series: dates must be unique and strictly ascending")
                immature = isinstance(count, int) and count < 20
                errors.extend(_require_null_immature_metrics(
                    portfolio,
                    ("total_return", "annualized_volatility", "max_drawdown", "current_drawdown", "longest_drawdown_duration", "rolling_20d_volatility", "positive_period_rate", "excess_return_vs_benchmark"),
                    f"shadow_health.portfolios.{name}", immature,
                ))
                if isinstance(observations, int) and count != observations:
                    errors.append(f"shadow_health.portfolios.{name}.observations: must equal shadow_health.observations")
                if not immature and isinstance(series, list) and series and isinstance(initial, (int, float)):
                    equities = [point.get("equity") for point in series if isinstance(point, dict)]
                    if len(equities) == len(series) and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in equities):
                        total = equities[-1] / initial - 1
                        peak = initial
                        worst = current = 0.0
                        longest = duration = 0
                        for value in equities:
                            if value >= peak:
                                peak, duration = value, 0
                            else:
                                duration += 1
                                longest = max(longest, duration)
                            current = 1 - value / peak
                            worst = max(worst, current)
                        for field, expected in (("total_return", total), ("max_drawdown", worst), ("current_drawdown", current), ("longest_drawdown_duration", longest)):
                            actual = portfolio.get(field)
                            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or abs(actual - expected) > 1e-10:
                                errors.append(f"shadow_health.portfolios.{name}.{field}: inconsistent with equity_series")
            if isinstance(fusion, dict):
                if shadow_health.get("return") != fusion.get("total_return"):
                    errors.append("shadow_health.return: must equal fusion total_return")
                if shadow_health.get("max_drawdown") != fusion.get("max_drawdown"):
                    errors.append("shadow_health.max_drawdown: must equal fusion max_drawdown")
    shadow_immature = isinstance(shadow_health, dict) and isinstance(shadow_health.get("observations"), int) and shadow_health["observations"] < 20
    errors.extend(_require_null_immature_metrics(shadow_health, ("return", "max_drawdown", "score"), "shadow_health", shadow_immature))
    cost_sensitivity = payload.get("cost_sensitivity")
    errors.extend(
        _require_null_immature_metrics(
            cost_sensitivity, ("score",), "cost_sensitivity", False
        )
    )
    cost_status_immature = (
        isinstance(cost_sensitivity, dict)
        and cost_sensitivity.get("status") in {"ACCUMULATING", "UNAVAILABLE"}
    )
    if isinstance(cost_sensitivity, dict) and cost_status_immature:
        reason = f"status is {cost_sensitivity['status']}"
        scenarios = cost_sensitivity.get("scenarios")
        if isinstance(scenarios, list):
            costs = []
            for index, scenario in enumerate(scenarios):
                if not isinstance(scenario, dict):
                    continue
                costs.append(scenario.get("one_way_cost"))
                for field in ("value", "annualized_return", "max_drawdown"):
                    if scenario.get(field) is not None:
                        errors.append(
                            f"cost_sensitivity.scenarios[{index}].{field}: must be null while {reason}"
                        )
            if costs != [0, 0.0005, 0.001, 0.002, 0.003]:
                errors.append("cost_sensitivity.scenarios: exact scenario costs must be sorted and unique")
        if cost_sensitivity.get("break_even_cost") is not None or cost_sensitivity.get("score") is not None:
            errors.append("cost_sensitivity: break_even_cost and score must be null while UNAVAILABLE")
    errors.extend(
        _require_null_immature_metrics(
            payload.get("overall"), ("score",), "overall", sample_immature
        )
    )
    return errors


def validate_us_compass_rotation_payload(
    payload: Any, schema: dict[str, Any] | None = None
) -> list[str]:
    errors = schema_errors(_schema("us-compass-rotation-map.schema.json", schema), payload) + unsafe_paths(payload)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        for index, item in enumerate(payload["items"]):
            trail = item.get("trail") if isinstance(item, dict) else None
            if not isinstance(trail, list):
                continue
            dates = [point.get("date") for point in trail if isinstance(point, dict)]
            if len(dates) != len(trail) or not all(valid_date(date) for date in dates):
                errors.append(f"items.{index}.trail: dates must use valid YYYY-MM-DD format")
            elif len(set(dates)) != len(dates):
                errors.append(f"items.{index}.trail: dates must be unique")
            elif dates != sorted(dates):
                errors.append(f"items.{index}.trail: dates must be strictly ascending")
    return errors


def validate_us_compass_risk_payload(
    payload: Any, schema: dict[str, Any] | None = None
) -> list[str]:
    errors = schema_errors(_schema("us-compass-risk.schema.json", schema), payload) + unsafe_paths(payload)
    if not isinstance(payload, dict):
        return errors
    symbols = payload.get("symbols")
    matrix = payload.get("correlation_matrix")
    if isinstance(symbols, list) and isinstance(matrix, list):
        size = len(symbols)
        if len(matrix) != size or any(not isinstance(row, list) or len(row) != size for row in matrix):
            errors.append("correlation_matrix dimension must match symbols NxN")
    symbol_set = set(symbols) if isinstance(symbols, list) else None
    for field in ("volatility", "risk_contribution"):
        section = payload.get(field)
        values = section.get("values") if isinstance(section, dict) else None
        if symbol_set is not None and isinstance(values, dict) and set(values) != symbol_set:
            errors.append(f"{field}.values keys must align with symbols")
    contribution = payload.get("risk_contribution")
    if isinstance(contribution, dict) and contribution.get("status") == "EVALUATED":
        values = contribution.get("values")
        if isinstance(values, dict) and values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in values.values()
        ):
            if abs(sum(values.values()) - 1.0) > 0.001:
                errors.append("risk_contribution.values sum must be within 0.001 of 1")
    return errors


def validate_catalog(data_dir: Path, catalog: dict[str, Any], errors: list[str]) -> None:
    datasets = catalog.get("datasets")
    if not isinstance(datasets, list):
        return
    ids = [item.get("dataset_id") for item in datasets if isinstance(item, dict)]
    active_specs = active_dataset_specs(data_dir, DATASETS)
    expected_ids = [spec.dataset_id for spec in active_specs]
    if ids != expected_ids:
        errors.append("catalog datasets must match the ordered active dataset registry")
    if len(ids) != len(set(ids)):
        errors.append("catalog contains duplicate dataset_id values")
    stable = {"schema_version": catalog.get("schema_version"), "contract_url": catalog.get("contract_url"), "datasets": datasets}
    if catalog.get("batch_id") != stable_batch_id(stable):
        errors.append("catalog batch_id differs from stable catalog semantics")

    by_id = {spec.dataset_id: spec for spec in active_specs}
    for item in datasets:
        if not isinstance(item, dict):
            continue
        dataset_id = item.get("dataset_id")
        spec = by_id.get(dataset_id)
        if spec is None:
            errors.append(f"catalog unknown dataset: {dataset_id!r}")
            continue
        if item.get("role") not in ROLES or item.get("role") != spec.role:
            errors.append(f"catalog {dataset_id} invalid role")
        if item.get("market") != spec.market:
            errors.append(f"catalog {dataset_id} invalid market")
        if not valid_date(item.get("observation_date")):
            errors.append(f"catalog {dataset_id} invalid observation_date")
        if item.get("public_url") != f"/data/{spec.relative_path}":
            errors.append(f"catalog {dataset_id} invalid public_url")
        categories = item.get("source_categories")
        if not isinstance(categories, list) or not categories or set(categories) - SOURCE_CATEGORIES:
            errors.append(f"catalog {dataset_id} contains invalid source_categories")
        complete = item.get("completeness") if isinstance(item.get("completeness"), dict) else {}
        status, ratio = complete.get("status"), complete.get("ratio")
        if status == "known" and (not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1):
            errors.append(f"catalog {dataset_id} known completeness requires ratio in [0,1]")
        if status == "unknown" and (ratio is not None or not complete.get("reason")):
            errors.append(f"catalog {dataset_id} unknown completeness requires null ratio and reason")
        degradation = item.get("degradation") if isinstance(item.get("degradation"), dict) else {}
        if degradation.get("status") in {"degraded", "unknown"} and not degradation.get("reasons"):
            errors.append(f"catalog {dataset_id} degradation must disclose reasons")
        path = data_dir / spec.relative_path
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            errors.append(f"catalog {dataset_id} target file is missing")
            continue
        if item.get("bytes") != len(raw):
            errors.append(f"catalog {dataset_id} bytes mismatch")
        if item.get("sha256") != hashlib.sha256(raw).hexdigest():
            errors.append(f"catalog {dataset_id} sha256 mismatch")
        try:
            payload = parse_json(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        errors.extend(f"{dataset_id} {message}" for message in unsafe_paths(payload))
        try:
            expected_entry = entry_for(data_dir, spec)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"catalog {dataset_id} cannot reconstruct metadata: {exc}")
        else:
            if item != expected_entry:
                errors.append(f"catalog {dataset_id} metadata differs from source dataset")


def validate_dashboard(data_dir: Path, dashboard: dict[str, Any], errors: list[str]) -> None:
    rows = dashboard.get("all_rows")
    if not isinstance(rows, list):
        return
    codes = [row.get("code") for row in rows if isinstance(row, dict)]
    if len(codes) != len(rows):
        errors.append("a-compass-dashboard rows must be objects")
    if len(codes) != len(set(codes)):
        errors.append("a-compass-dashboard contains duplicate codes")
    expected_count = dashboard.get("summary", {}).get("universe_count") if isinstance(dashboard.get("summary"), dict) else None
    if not isinstance(expected_count, int) or expected_count != len(rows):
        errors.append("a-compass-dashboard row count differs from summary.universe_count")
    semantic = {key: dashboard.get(key) for key in ("run_date", "evaluation_date", "latest_trade_date", "summary", "market_regime", "realtime_scope", "snapshot_scope", "all_rows")}
    try:
        expected_batch = dashboard_batch_id(semantic)
    except (TypeError, ValueError) as exc:
        errors.append(f"a-compass-dashboard batch_id cannot be calculated: {exc}")
    else:
        if dashboard.get("batch_id") != expected_batch:
            errors.append("a-compass-dashboard batch_id differs from current semantic data")
    for index, row in enumerate(rows):
        if isinstance(row, dict) and set(row) != set(A_FIELDS):
            errors.append(f"a-compass-dashboard row[{index}] public field set is incomplete or contains unknown fields")
    try:
        pool = parse_json(data_dir / "etf-garden-pool.json")
        if not isinstance(pool, dict):
            raise ValueError("etf-garden-pool root must be an object")
        expected_dashboard = build_dashboard_payload(pool)
    except ValueError as exc:
        errors.append(f"a-compass-dashboard cannot reconstruct source export: {exc}")
    else:
        if dashboard != expected_dashboard:
            errors.append("a-compass-dashboard differs from etf-garden-pool export")


def validate(data_dir: Path = DATA, schema_dir: Path = SCHEMAS) -> ValidationResult:
    errors: list[str] = []
    schemas = validate_schema_files(schema_dir, errors)
    try:
        catalog = parse_json(data_dir / "catalog.json")
        dashboard = parse_json(data_dir / "a-compass-dashboard.json")
        research_layer = parse_json(data_dir / "research/investment-research-layer.json")
    except ValueError as exc:
        return ValidationResult("error", errors + [str(exc)])
    if not isinstance(catalog, dict) or not isinstance(dashboard, dict) or not isinstance(research_layer, dict):
        return ValidationResult("error", errors + ["catalog, dashboard and research layer roots must be objects"])
    if "data-catalog.schema.json" in schemas:
        errors.extend(f"catalog schema: {message}" for message in schema_errors(schemas["data-catalog.schema.json"], catalog))
    if "a-compass-dashboard.schema.json" in schemas:
        errors.extend(f"dashboard schema: {message}" for message in schema_errors(schemas["a-compass-dashboard.schema.json"], dashboard))
    if "investment-research-layer.schema.json" in schemas:
        errors.extend(f"research layer schema: {message}" for message in schema_errors(schemas["investment-research-layer.schema.json"], research_layer))
    errors.extend(f"catalog {message}" for message in unsafe_paths(catalog))
    errors.extend(f"a-compass-dashboard {message}" for message in unsafe_paths(dashboard))
    errors.extend(f"investment-research-layer {message}" for message in unsafe_paths(research_layer))
    validate_catalog(data_dir, catalog, errors)
    validate_dashboard(data_dir, dashboard, errors)
    return ValidationResult("ok" if not errors else "error", errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--schema-dir", type=Path, default=SCHEMAS)
    args = parser.parse_args()
    result = validate(args.data_dir, args.schema_dir)
    print(json.dumps(result._asdict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
