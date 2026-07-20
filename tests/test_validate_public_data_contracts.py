import importlib.util
import json
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/validate_public_data_contracts.py"
spec = importlib.util.spec_from_file_location("validate_public_data_contracts", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "scripts/generate_data_catalog.py"
catalog_spec = importlib.util.spec_from_file_location("generate_data_catalog_for_tests", CATALOG_PATH)
assert catalog_spec and catalog_spec.loader
catalog_module = importlib.util.module_from_spec(catalog_spec)
sys.modules[catalog_spec.name] = catalog_module
catalog_spec.loader.exec_module(catalog_module)


def write_json(path: Path, payload: dict, *, allow_nan: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=allow_nan) + "\n", encoding="utf-8")


def valid_dashboard() -> dict:
    rows = [
        {
            "code": "510300", "name": "沪深300ETF", "type": "宽基", "theme": "大盘",
            "status": "core", "price": 4.1, "ret5": 1.2, "ret20": 2.3,
            "close_position": 0.7, "signal_score": 60.0, "strength_level": "B",
            "trading_risk_score": 20.0, "trade_state": "观察", "action": "观察",
            "cooldown_state": "正常", "risk_flags": [], "risk_level": "低",
            "agent_bull": [], "agent_bear": [], "agent_scores": {},
        }
    ]
    semantic = {
        "run_date": "2026-07-20", "evaluation_date": "2026-07-20",
        "latest_trade_date": "2026-07-20", "summary": {"universe_count": 1},
        "market_regime": {}, "realtime_scope": [], "snapshot_scope": [], "all_rows": rows,
    }
    return {
        "schema_version": "a-compass-dashboard-v1",
        "batch_id": module.dashboard_batch_id(semantic),
        "contract_url": "/schemas/a-compass-dashboard.schema.json",
        "generated_at": "2026-07-20T22:00:00+08:00",
        **semantic,
    }


def prepare_tree(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    schemas = tmp_path / "schemas"
    source_schemas = Path(__file__).resolve().parents[1] / "public/schemas"
    schemas.mkdir()
    for path in source_schemas.glob("*.schema.json"):
        (schemas / path.name).write_bytes(path.read_bytes())
    for item in catalog_module.DATASETS:
        payload = {
            "date": "2026-07-20", "generated_at": "2026-07-20T22:00:00+08:00",
            "updated_at": "2026-07-20T22:00:00+08:00", "trade_date": "2026-07-20",
            "latest_trade_date": "2026-07-20", "evaluation_date": "2026-07-20", "run_date": "2026-07-20",
            "model_date": "2026-07-20", "quote_trade_date": "2026-07-20",
            "as_of": "2026-07-20",
            "dataset": {"as_of": "2026-07-20"},
            "accounts": {"A": {"history": [{"date": "2026-07-20"}]}},
            "summary": {"universe_count": 1, "valid_count": 1}, "data_quality": {"failed": 0},
            "all_rows": valid_dashboard()["all_rows"], "rows": [{"symbol": "SPY"}],
        }
        write_json(data / item.relative_path, payload)
    dashboard = module.build_dashboard_payload(json.loads((data / "etf-garden-pool.json").read_text(encoding="utf-8")))
    write_json(data / "a-compass-dashboard.json", dashboard)
    write_json(data / "catalog.json", catalog_module.build_catalog(data, generated_at="2026-07-20T22:01:00Z"))
    return data, schemas


def test_valid_contract_tree_passes(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    result = module.validate(data, schemas)
    assert result.errors == []


def test_hash_tampering_fails_closed(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    target = data / "us-etf-pool.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    result = module.validate(data, schemas)
    assert any("sha256" in error or "bytes" in error for error in result.errors)


@pytest.mark.parametrize("bad_key", ["api_key", "token", "checkpoint", "private_path"])
def test_sensitive_keys_are_rejected_recursively(tmp_path, bad_key):
    data, schemas = prepare_tree(tmp_path)
    dashboard = json.loads((data / "a-compass-dashboard.json").read_text(encoding="utf-8"))
    dashboard["all_rows"][0][bad_key] = "/root/private" if "path" in bad_key else "secret"
    write_json(data / "a-compass-dashboard.json", dashboard)
    result = module.validate(data, schemas)
    assert any("forbidden" in error for error in result.errors)


def test_non_finite_and_html_delimiter_are_rejected(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    dashboard = json.loads((data / "a-compass-dashboard.json").read_text(encoding="utf-8"))
    dashboard["all_rows"][0]["price"] = float("nan")
    dashboard["all_rows"][0]["name"] = "<script>bad</script>"
    write_json(data / "a-compass-dashboard.json", dashboard, allow_nan=True)
    result = module.validate(data, schemas)
    assert any("non-finite" in error for error in result.errors)
    assert any("HTML" in error for error in result.errors)


def test_dashboard_duplicate_codes_and_batch_drift_are_rejected(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    dashboard = json.loads((data / "a-compass-dashboard.json").read_text(encoding="utf-8"))
    dashboard["all_rows"].append(dict(dashboard["all_rows"][0]))
    dashboard["summary"]["universe_count"] = 2
    result_path = data / "a-compass-dashboard.json"
    write_json(result_path, dashboard)
    result = module.validate(data, schemas)
    assert any("duplicate" in error for error in result.errors)
    assert any("batch_id" in error for error in result.errors)


def test_invalid_observation_date_is_rejected(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    catalog = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    catalog["datasets"][0]["observation_date"] = "2026-02-30"
    write_json(data / "catalog.json", catalog)
    result = module.validate(data, schemas)
    assert any("observation_date" in error for error in result.errors)


@pytest.mark.parametrize("field,bad_value", [
    ("schema_version", "forged-v9"),
    ("observation_date", "2026-07-19"),
    ("generated_at", "2026-07-19T00:00:00Z"),
    ("source_categories", ["publication_receipt"]),
    ("completeness", {"status": "known", "ratio": 0.5, "observed": 1, "expected": 2, "reason": None}),
    ("degradation", {"status": "degraded", "reasons": ["forged"]}),
])
def test_catalog_metadata_must_match_source_dataset(tmp_path, field, bad_value):
    data, schemas = prepare_tree(tmp_path)
    catalog = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    catalog["datasets"][0][field] = bad_value
    stable = {"schema_version": catalog["schema_version"], "contract_url": catalog["contract_url"], "datasets": catalog["datasets"]}
    catalog["batch_id"] = catalog_module.stable_batch_id(stable)
    write_json(data / "catalog.json", catalog)
    result = module.validate(data, schemas)
    assert any("metadata differs from source dataset" in error for error in result.errors)


def test_schema_files_are_scanned_for_private_paths_and_html(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    target = schemas / "decision-drift.schema.json"
    schema = json.loads(target.read_text(encoding="utf-8"))
    schema["description"] = "/root/private <b>unsafe</b>"
    write_json(target, schema)
    result = module.validate(data, schemas)
    assert any("schema decision-drift.schema.json" in error and ("private path" in error or "HTML" in error) for error in result.errors)


@pytest.mark.parametrize("bad_key", ["access_key", "model_path", "revision", "device"])
def test_internal_metadata_keys_are_rejected(tmp_path, bad_key):
    data, schemas = prepare_tree(tmp_path)
    dashboard = json.loads((data / "a-compass-dashboard.json").read_text(encoding="utf-8"))
    dashboard["all_rows"][0]["agent_scores"][bad_key] = "prefix:/root/private/model.bin"
    dashboard["batch_id"] = module.dashboard_batch_id(dashboard)
    write_json(data / "a-compass-dashboard.json", dashboard)
    write_json(data / "catalog.json", catalog_module.build_catalog(data, generated_at="2026-07-20T22:01:00Z"))
    result = module.validate(data, schemas)
    assert any("forbidden public key" in error or "private path" in error for error in result.errors)


def test_stale_dashboard_is_rejected_even_when_internally_consistent(tmp_path):
    data, schemas = prepare_tree(tmp_path)
    dashboard = json.loads((data / "a-compass-dashboard.json").read_text(encoding="utf-8"))
    dashboard["all_rows"][0]["price"] = 9.99
    dashboard["batch_id"] = module.dashboard_batch_id(dashboard)
    write_json(data / "a-compass-dashboard.json", dashboard)
    write_json(data / "catalog.json", catalog_module.build_catalog(data, generated_at="2026-07-20T22:01:00Z"))
    result = module.validate(data, schemas)
    assert any("differs from etf-garden-pool export" in error for error in result.errors)
