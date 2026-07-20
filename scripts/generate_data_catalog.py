#!/usr/bin/env python3
"""Build the public dataset catalog from stable, public metadata."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
CATALOG_SCHEMA_VERSION = "data-catalog-v1"
CATALOG_CONTRACT_URL = "/schemas/data-catalog.schema.json"


class DatasetSpec(NamedTuple):
    dataset_id: str
    relative_path: str
    role: str
    market: str
    schema_version: str
    source_categories: tuple[str, ...]
    observation_fields: tuple[str, ...]
    generated_fields: tuple[str, ...]


DATASETS = (
    DatasetSpec("garden-recommendations", "garden-recommendations.json", "production", "CN", "legacy-unversioned", ("market_data", "derived_research"), ("date", "applies_to", "level_data_as_of"), ("updated_at",)),
    DatasetSpec("a-compass-dashboard", "a-compass-dashboard.json", "export", "CN", "a-compass-dashboard-v1", ("market_data", "derived_research"), ("latest_trade_date", "evaluation_date", "run_date"), ("generated_at",)),
    DatasetSpec("etf-garden-pool", "etf-garden-pool.json", "runtime", "CN", "legacy-unversioned", ("market_data", "derived_research"), ("latest_trade_date", "evaluation_date", "run_date"), ("generated_at",)),
    DatasetSpec("a-share-mid-macro", "a-share-mid-macro.json", "production", "CN", "a-share-mid-macro-v2", ("market_data", "official_statistics", "derived_research"), ("__latest_as_of__",), ("generated_at",)),
    DatasetSpec("a-share-research-audit", "model-lab/a-share-research-audit.json", "shadow", "CN", "research-audit-v1", ("historical_market_data", "derived_research"), ("dataset.as_of",), ("generated_at",)),
    DatasetSpec("a-share-path-shadow", "model-lab/a-share-path-shadow.json", "shadow", "CN", "a-share-path-shadow-v1", ("historical_market_data", "model_output"), ("latest_trade_date",), ("generated_at",)),
    DatasetSpec("us-etf-garden", "us-etf-garden.json", "production", "US", "legacy-unversioned", ("market_data", "derived_research"), ("date",), ("updated_at",)),
    DatasetSpec("us-etf-pool", "us-etf-pool.json", "runtime", "US", "legacy-unversioned", ("market_data", "derived_research"), ("quote_trade_date", "model_date"), ("generated_at",)),
    DatasetSpec("us-macro-dashboard", "us-macro-dashboard.json", "production", "US", "us-macro-dashboard-v2", ("market_data", "official_statistics", "public_events"), ("__latest_as_of__",), ("generated_at",)),
    DatasetSpec("paper-trading", "paper-trading.json", "history", "MULTI", "paper-trading-v1", ("simulated_execution", "derived_research"), ("__paper_history__",), ("updated_at",)),
    DatasetSpec("a-share-nightly-deployment", "a-share-nightly-deployment.json", "history", "CN", "a-share-nightly-deployment-v1", ("publication_receipt",), ("trade_date",), ()),
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def stable_batch_id(payload: dict[str, Any]) -> str:
    raw_datasets = payload.get("datasets")
    datasets: list[Any] = raw_datasets if isinstance(raw_datasets, list) else []
    stable_datasets: list[Any] = []
    for item in datasets:
        if not isinstance(item, dict):
            stable_datasets.append(item)
            continue
        stable_datasets.append({
            key: value for key, value in item.items()
            if key not in {"generated_at", "sha256", "bytes"}
        })
    stable = {
        "schema_version": payload.get("schema_version"),
        "contract_url": payload.get("contract_url"),
        "datasets": stable_datasets,
    }
    return hashlib.sha256(canonical_bytes(stable)).hexdigest()


def nested_get(payload: dict[str, Any], field: str) -> Any:
    value: Any = payload
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def date_prefix(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date().isoformat()
    except ValueError:
        return None


def first_value(payload: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = nested_get(payload, field)
        if value not in (None, "", []):
            return value
    return None


def latest_as_of(value: Any) -> str | None:
    dates: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key == "as_of":
                    parsed = date_prefix(child)
                    if parsed:
                        dates.append(parsed)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return max(dates, default=None)


def latest_paper_history_date(payload: dict[str, Any]) -> str | None:
    dates: list[str] = []
    accounts = payload.get("accounts")
    if isinstance(accounts, dict):
        for account in accounts.values():
            if not isinstance(account, dict):
                continue
            history = account.get("history")
            if not isinstance(history, list):
                continue
            for item in history:
                if isinstance(item, dict):
                    parsed = date_prefix(item.get("date"))
                    if parsed:
                        dates.append(parsed)
    return max(dates, default=None)


def observation_date_for(payload: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    if fields == ("__latest_as_of__",):
        return latest_as_of(payload)
    if fields == ("__paper_history__",):
        return latest_paper_history_date(payload)
    return date_prefix(first_value(payload, fields))


def semantic_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: semantic_payload(child)
            for key, child in value.items()
            if key not in {"generated_at", "updated_at"}
        }
    if isinstance(value, list):
        return [semantic_payload(child) for child in value]
    return value


def completeness(payload: dict[str, Any]) -> dict[str, Any]:
    raw_summary = payload.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    total = summary.get("universe_count") if "universe_count" in summary else summary.get("universe")
    valid = summary.get("valid_count") if "valid_count" in summary else summary.get("valid")
    if not isinstance(total, int) or total < 0:
        rows = payload.get("all_rows") if isinstance(payload.get("all_rows"), list) else payload.get("rows")
        if isinstance(rows, list):
            total = len(rows)
            valid = len(rows)
    if isinstance(total, int) and total > 0 and isinstance(valid, int):
        ratio = max(0.0, min(1.0, valid / total))
        return {"status": "known", "ratio": round(ratio, 6), "observed": valid, "expected": total, "reason": None}
    return {"status": "unknown", "ratio": None, "observed": None, "expected": None, "reason": "dataset does not publish a comparable observed/expected count"}


def degradation(payload: dict[str, Any], complete: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), dict) else {}
    failed = quality.get("failed")
    fallback = None
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if isinstance(summary.get("raw_fallback_count"), int):
        fallback = summary["raw_fallback_count"]
    if isinstance(failed, int) and failed > 0:
        return {"status": "degraded", "reasons": [f"{failed} public source observations unavailable"]}
    if isinstance(fallback, int) and fallback > 0:
        return {"status": "degraded", "reasons": [f"{fallback} observations use a disclosed fallback"]}
    if complete["status"] == "known" and complete["ratio"] < 1:
        return {"status": "degraded", "reasons": ["published completeness is below 100%"]}
    if complete["status"] == "unknown":
        return {"status": "unknown", "reasons": [complete["reason"]]}
    return {"status": "normal", "reasons": []}


def entry_for(data_dir: Path, spec: DatasetSpec) -> dict[str, Any]:
    path = data_dir / spec.relative_path
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{spec.relative_path} root must be an object")
    observation_date = observation_date_for(payload, spec.observation_fields)
    if observation_date is None:
        raise ValueError(f"{spec.relative_path} has no valid observation date")
    generated_at = first_value(payload, spec.generated_fields)
    complete = completeness(payload)
    return {
        "dataset_id": spec.dataset_id,
        "role": spec.role,
        "market": spec.market,
        "schema_version": payload.get("schema_version") or spec.schema_version,
        "observation_date": observation_date,
        "generated_at": generated_at,
        "completeness": complete,
        "degradation": degradation(payload, complete),
        "source_categories": list(spec.source_categories),
        "semantic_sha256": hashlib.sha256(canonical_bytes(semantic_payload(payload))).hexdigest(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "public_url": f"/data/{spec.relative_path}",
    }


def parse_generated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if text.endswith(" CST"):
        text = text[:-4] + "+08:00"
    text = text.replace(" UTC+", "+")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def catalog_generated_at(entries: list[dict[str, Any]]) -> str:
    timestamps = [parse_generated_at(item.get("generated_at")) for item in entries]
    concrete = [item for item in timestamps if item is not None]
    if concrete:
        return max(concrete).isoformat(timespec="seconds").replace("+00:00", "Z")
    observation_dates = [date_prefix(item.get("observation_date")) for item in entries]
    dates = [item for item in observation_dates if item is not None]
    if not dates:
        raise ValueError("catalog has no generation timestamp or observation date")
    fallback = datetime.fromisoformat(max(dates)).replace(tzinfo=timezone.utc) + timedelta(hours=23, minutes=59, seconds=59)
    return fallback.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_catalog(data_dir: Path = DATA, *, generated_at: str | None = None) -> dict[str, Any]:
    entries = [entry_for(data_dir, spec) for spec in DATASETS]
    stable = {"schema_version": CATALOG_SCHEMA_VERSION, "contract_url": CATALOG_CONTRACT_URL, "datasets": entries}
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "batch_id": stable_batch_id(stable),
        "contract_url": CATALOG_CONTRACT_URL,
        "generated_at": generated_at or catalog_generated_at(entries),
        "datasets": entries,
    }


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.data_dir / "catalog.json"
    catalog = build_catalog(args.data_dir)
    write_atomic(output, catalog)
    print(json.dumps({"status": "ok", "path": str(output), "batch_id": catalog["batch_id"], "datasets": len(catalog["datasets"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
