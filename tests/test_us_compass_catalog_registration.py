from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG_SCRIPT = ROOT / "scripts" / "generate_data_catalog.py"
RELEASE_SCRIPT = ROOT / "scripts" / "pages_release.py"
VALIDATE_SCRIPT = ROOT / "scripts" / "validate_public_data_contracts.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalog = load_module("task4_generate_data_catalog", CATALOG_SCRIPT)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_registry_contains_staged_us_compass_datasets_with_exact_metadata():
    staged = [spec for spec in catalog.DATASETS if not spec.required]

    assert staged == [
        catalog.DatasetSpec(
            "us-compass-health",
            "us-compass-health.json",
            "shadow",
            "US",
            "us-compass-health-v1",
            ("historical_market_data", "model_output", "derived_research"),
            ("model_date",),
            ("generated_at",),
            False,
        ),
        catalog.DatasetSpec(
            "us-compass-rotation-map",
            "us-compass-rotation-map.json",
            "shadow",
            "US",
            "us-compass-rotation-map-v1",
            ("historical_market_data", "derived_research"),
            ("as_of",),
            (),
            False,
        ),
        catalog.DatasetSpec(
            "us-compass-risk",
            "us-compass-risk.json",
            "shadow",
            "US",
            "us-compass-risk-v1",
            ("historical_market_data", "derived_research"),
            ("as_of",),
            (),
            False,
        ),
    ]


def minimal_payload(observation_field: str) -> dict:
    return {
        "schema_version": "test-v1",
        observation_field: "2026-08-07",
        "generated_at": "2026-08-08T02:00:00Z",
    }


def test_active_dataset_specs_omits_absent_optional_files_but_keeps_required(tmp_path):
    required = catalog.DatasetSpec(
        "required", "required.json", "shadow", "US", "test-v1", ("derived_research",), ("as_of",), ()
    )
    optional = catalog.DatasetSpec(
        "optional", "optional.json", "shadow", "US", "test-v1", ("derived_research",), ("as_of",), (), False
    )

    assert catalog.active_dataset_specs(tmp_path, (required, optional)) == (required,)


def test_build_catalog_includes_optional_file_when_present(monkeypatch, tmp_path):
    required = catalog.DatasetSpec(
        "required", "required.json", "shadow", "US", "required-v1", ("derived_research",), ("as_of",), ()
    )
    optional = catalog.DatasetSpec(
        "optional", "optional.json", "shadow", "US", "optional-v1", ("derived_research",), ("as_of",), (), False
    )
    monkeypatch.setattr(catalog, "DATASETS", (required, optional))
    write_json(tmp_path / "required.json", minimal_payload("as_of"))
    write_json(tmp_path / "optional.json", {**minimal_payload("as_of"), "schema_version": "optional-v1"})

    result = catalog.build_catalog(tmp_path, generated_at="2026-08-08T02:00:00Z")

    assert [item["dataset_id"] for item in result["datasets"]] == ["required", "optional"]
    assert result["datasets"][1]["public_url"] == "/data/optional.json"
    assert result["datasets"][1]["schema_version"] == "optional-v1"


def test_present_malformed_optional_file_fails_closed(monkeypatch, tmp_path):
    optional = catalog.DatasetSpec(
        "optional", "optional.json", "shadow", "US", "optional-v1", ("derived_research",), ("as_of",), (), False
    )
    monkeypatch.setattr(catalog, "DATASETS", (optional,))
    (tmp_path / "optional.json").write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        catalog.build_catalog(tmp_path, generated_at="2026-08-08T02:00:00Z")


def test_missing_required_file_still_fails_closed(monkeypatch, tmp_path):
    required = catalog.DatasetSpec(
        "required", "required.json", "shadow", "US", "required-v1", ("derived_research",), ("as_of",), ()
    )
    monkeypatch.setattr(catalog, "DATASETS", (required,))

    with pytest.raises(FileNotFoundError):
        catalog.build_catalog(tmp_path, generated_at="2026-08-08T02:00:00Z")


def test_pages_release_owns_future_us_compass_paths():
    release = load_module("task4_pages_release", RELEASE_SCRIPT)

    assert {
        "public/data/us-compass-health.json",
        "public/data/us-compass-rotation-map.json",
        "public/data/us-compass-risk.json",
    } <= release.EXTERNAL_DIRTY


def test_validate_catalog_uses_active_registry_order(monkeypatch, tmp_path):
    validator = load_module("task4_validate_public_data_contracts", VALIDATE_SCRIPT)
    required = catalog.DatasetSpec(
        "required", "required.json", "shadow", "US", "required-v1", ("derived_research",), ("as_of",), ()
    )
    optional = catalog.DatasetSpec(
        "optional", "optional.json", "shadow", "US", "optional-v1", ("derived_research",), ("as_of",), (), False
    )
    write_json(tmp_path / "required.json", minimal_payload("as_of"))
    monkeypatch.setattr(validator, "DATASETS", (required, optional))
    monkeypatch.setattr(validator, "active_dataset_specs", catalog.active_dataset_specs)
    entry = catalog.entry_for(tmp_path, required)
    stable = {
        "schema_version": catalog.CATALOG_SCHEMA_VERSION,
        "contract_url": catalog.CATALOG_CONTRACT_URL,
        "datasets": [entry],
    }
    payload = {**stable, "batch_id": catalog.stable_batch_id(stable)}
    errors: list[str] = []

    validator.validate_catalog(tmp_path, payload, errors)

    assert errors == []


def test_validate_catalog_rejects_missing_active_optional_dataset(monkeypatch, tmp_path):
    validator = load_module("task4_validate_public_data_contracts_present", VALIDATE_SCRIPT)
    required = catalog.DatasetSpec(
        "required", "required.json", "shadow", "US", "required-v1", ("derived_research",), ("as_of",), ()
    )
    optional = catalog.DatasetSpec(
        "optional", "optional.json", "shadow", "US", "optional-v1", ("derived_research",), ("as_of",), (), False
    )
    write_json(tmp_path / "required.json", minimal_payload("as_of"))
    write_json(tmp_path / "optional.json", minimal_payload("as_of"))
    monkeypatch.setattr(validator, "DATASETS", (required, optional))
    monkeypatch.setattr(validator, "active_dataset_specs", catalog.active_dataset_specs)
    entry = catalog.entry_for(tmp_path, required)
    stable = {
        "schema_version": catalog.CATALOG_SCHEMA_VERSION,
        "contract_url": catalog.CATALOG_CONTRACT_URL,
        "datasets": [entry],
    }
    payload = {**stable, "batch_id": catalog.stable_batch_id(stable)}
    errors: list[str] = []

    validator.validate_catalog(tmp_path, payload, errors)

    assert "catalog datasets must match the ordered active dataset registry" in errors
