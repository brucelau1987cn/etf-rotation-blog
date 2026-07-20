#!/usr/bin/env python3
"""Fail-closed validation for public schemas, catalog metadata and dashboard payloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:
    from generate_data_catalog import DATASETS, entry_for, stable_batch_id
    from generate_public_dashboard_payloads import A_FIELDS, build_payload as build_dashboard_payload, dashboard_batch_id
except ModuleNotFoundError:
    from scripts.generate_data_catalog import DATASETS, entry_for, stable_batch_id
    from scripts.generate_public_dashboard_payloads import A_FIELDS, build_payload as build_dashboard_payload, dashboard_batch_id

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
SCHEMAS = ROOT / "public/schemas"
SCHEMA_FILES = (
    "data-catalog.schema.json", "a-compass-dashboard.schema.json",
    "forward-evidence-ledger.schema.json", "decision-thesis.schema.json", "decision-drift.schema.json",
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


def validate_catalog(data_dir: Path, catalog: dict[str, Any], errors: list[str]) -> None:
    datasets = catalog.get("datasets")
    if not isinstance(datasets, list):
        return
    ids = [item.get("dataset_id") for item in datasets if isinstance(item, dict)]
    expected_ids = [spec.dataset_id for spec in DATASETS]
    if ids != expected_ids:
        errors.append("catalog datasets must match the ordered core dataset registry")
    if len(ids) != len(set(ids)):
        errors.append("catalog contains duplicate dataset_id values")
    stable = {"schema_version": catalog.get("schema_version"), "contract_url": catalog.get("contract_url"), "datasets": datasets}
    if catalog.get("batch_id") != stable_batch_id(stable):
        errors.append("catalog batch_id differs from stable catalog semantics")

    by_id = {spec.dataset_id: spec for spec in DATASETS}
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
    except ValueError as exc:
        return ValidationResult("error", errors + [str(exc)])
    if not isinstance(catalog, dict) or not isinstance(dashboard, dict):
        return ValidationResult("error", errors + ["catalog and dashboard roots must be objects"])
    if "data-catalog.schema.json" in schemas:
        errors.extend(f"catalog schema: {message}" for message in schema_errors(schemas["data-catalog.schema.json"], catalog))
    if "a-compass-dashboard.schema.json" in schemas:
        errors.extend(f"dashboard schema: {message}" for message in schema_errors(schemas["a-compass-dashboard.schema.json"], dashboard))
    errors.extend(f"catalog {message}" for message in unsafe_paths(catalog))
    errors.extend(f"a-compass-dashboard {message}" for message in unsafe_paths(dashboard))
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
