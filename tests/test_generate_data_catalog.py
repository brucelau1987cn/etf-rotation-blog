import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/generate_data_catalog.py"
spec = importlib.util.spec_from_file_location("generate_data_catalog", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def test_stable_batch_id_ignores_generation_time():
    first = {"dataset_id": "sample", "observation_date": "2026-07-20", "sha256": "a" * 64, "bytes": 12}
    second = {**first, "generated_at": "2026-07-20T23:59:00+08:00"}
    assert module.stable_batch_id(first) == module.stable_batch_id(second)


def test_stable_batch_id_ignores_integrity_fields_but_keeps_semantics():
    first = {"schema_version": "data-catalog-v1", "contract_url": "/schema", "datasets": [{
        "dataset_id": "sample", "generated_at": "first", "sha256": "a" * 64, "bytes": 10,
        "observation_date": "2026-07-20", "role": "production",
    }]}
    rerun = {"schema_version": "data-catalog-v1", "contract_url": "/schema", "datasets": [{
        **first["datasets"][0], "generated_at": "second", "sha256": "b" * 64, "bytes": 11,
    }]}
    changed = {"schema_version": "data-catalog-v1", "contract_url": "/schema", "datasets": [{
        **first["datasets"][0], "observation_date": "2026-07-21",
    }]}
    assert module.stable_batch_id(first) == module.stable_batch_id(rerun)
    assert module.stable_batch_id(first) != module.stable_batch_id(changed)


def test_build_catalog_is_deterministic_and_complete(tmp_path):
    data = tmp_path / "data"
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    for spec_item in module.DATASETS:
        payload = {
            "date": "2026-07-20",
            "generated_at": "2026-07-20T22:00:00+08:00",
            "updated_at": "2026-07-20T22:00:00+08:00",
            "trade_date": "2026-07-20",
            "latest_trade_date": "2026-07-20",
            "model_date": "2026-07-20",
            "quote_trade_date": "2026-07-20",
            "evaluation_date": "2026-07-20",
            "as_of": "2026-07-20",
            "dataset": {"as_of": "2026-07-20"},
            "accounts": {"A": {"history": [{"date": "2026-07-20"}]}},
            "summary": {"universe_count": 2, "valid_count": 2},
            "data_quality": {"failed": 0},
            "all_rows": [{"code": "1"}, {"code": "2"}],
            "rows": [{"symbol": "A"}, {"symbol": "B"}],
        }
        write_json(data / spec_item.relative_path, payload)
    first = module.build_catalog(data, generated_at="2026-07-20T12:00:00Z")
    second = module.build_catalog(data, generated_at="2026-07-20T13:00:00Z")
    assert first["batch_id"] == second["batch_id"]
    assert len(first["datasets"]) == len(module.DATASETS) >= 11
    required = {"dataset_id", "role", "market", "schema_version", "observation_date", "generated_at", "completeness", "degradation", "source_categories", "sha256", "bytes", "public_url"}
    assert all(required <= set(item) for item in first["datasets"])
    assert all(0 <= item["completeness"]["ratio"] <= 1 for item in first["datasets"])


def test_write_atomic_does_not_leave_temporary_file(tmp_path):
    output = tmp_path / "catalog.json"
    module.write_atomic(output, {"schema_version": "data-catalog-v1", "datasets": []})
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "data-catalog-v1"
    assert not output.with_suffix(".json.tmp").exists()


def test_macro_observation_date_uses_latest_real_observation_not_generation_time(tmp_path):
    path = tmp_path / "a-share-mid-macro.json"
    write_json(path, {
        "generated_at": "2030-01-02T22:00:00+08:00",
        "framework": [
            {"as_of": "2026-07-20", "observations": [{"as_of": "2026-06-01"}]},
            {"as_of": "2026-07-17"},
        ],
    })
    spec_item = next(item for item in module.DATASETS if item.dataset_id == "a-share-mid-macro")
    first = module.entry_for(tmp_path, spec_item)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generated_at"] = "2031-02-03T22:00:00+08:00"
    write_json(path, payload)
    second = module.entry_for(tmp_path, spec_item)
    assert first["observation_date"] == second["observation_date"] == "2026-07-20"
    first_catalog = {"schema_version": "data-catalog-v1", "contract_url": "/schema", "datasets": [first]}
    second_catalog = {"schema_version": "data-catalog-v1", "contract_url": "/schema", "datasets": [second]}
    assert module.stable_batch_id(first_catalog) == module.stable_batch_id(second_catalog)


@pytest.mark.parametrize("dataset_id", ["a-share-mid-macro", "us-macro-dashboard"])
def test_macro_without_valid_as_of_fails_closed(tmp_path, dataset_id):
    spec_item = next(item for item in module.DATASETS if item.dataset_id == dataset_id)
    write_json(tmp_path / spec_item.relative_path, {
        "generated_at": "2030-01-02T22:00:00+08:00",
        "framework": [],
        "dimensions": [{"as_of": "invalid"}],
    })
    with pytest.raises(ValueError, match="no valid observation date"):
        module.entry_for(tmp_path, spec_item)


def test_zero_valid_count_is_known_zero_completeness():
    result = module.completeness({"summary": {"universe_count": 10, "valid_count": 0}})
    assert result == {"status": "known", "ratio": 0.0, "observed": 0, "expected": 10, "reason": None}


def test_atomic_writer_supports_concurrent_writers(tmp_path):
    output = tmp_path / "catalog.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: module.write_atomic(output, {"value": index}), range(64)))
    assert json.loads(output.read_text(encoding="utf-8"))["value"] in range(64)
    assert list(tmp_path.glob(".catalog.json.*.tmp")) == []


def test_semantic_hash_changes_with_content_and_ignores_generation_time():
    first = {"generated_at": "first", "rows": [{"value": 1}]}
    rerun = {"generated_at": "second", "rows": [{"value": 1}]}
    changed = {"generated_at": "second", "rows": [{"value": 2}]}
    assert module.canonical_bytes(module.semantic_payload(first)) == module.canonical_bytes(module.semantic_payload(rerun))
    assert module.canonical_bytes(module.semantic_payload(first)) != module.canonical_bytes(module.semantic_payload(changed))
