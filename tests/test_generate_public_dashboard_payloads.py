import importlib.util
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts/generate_public_dashboard_payloads.py"
spec = importlib.util.spec_from_file_location("generate_public_dashboard_payloads", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_pick_keeps_only_public_dashboard_fields():
    row = {"code": "510300", "name": "沪深300ETF", "price": 4.1, "secret": "drop"}
    result = module.pick(row, ("code", "name", "price"))
    assert result == {"code": "510300", "name": "沪深300ETF", "price": 4.1}


def test_write_atomic_outputs_compact_utf8_json(tmp_path):
    output = tmp_path / "dashboard.json"
    module.write_atomic(output, {"name": "罗盘", "rows": [{"code": "510300"}]})
    assert json.loads(output.read_text(encoding="utf-8"))["name"] == "罗盘"
    assert not output.with_suffix(".json.tmp").exists()
    assert "罗盘" in output.read_text(encoding="utf-8")


def test_build_payload_adds_versioned_contract_without_removing_existing_fields():
    source = {
        "generated_at": "2026-07-20T22:00:00+08:00",
        "run_date": "2026-07-20",
        "evaluation_date": "2026-07-20",
        "latest_trade_date": "2026-07-20",
        "summary": {"universe_count": 1},
        "market_regime": {"state": "弱"},
        "realtime_scope": ["当前价"],
        "snapshot_scope": ["综合分"],
        "all_rows": [{"code": "510300", "name": "沪深300ETF", "price": 4.1, "secret": "drop"}],
    }
    payload = module.build_payload(source)
    assert payload["schema_version"] == "a-compass-dashboard-v1"
    assert payload["contract_url"] == "/schemas/a-compass-dashboard.schema.json"
    assert len(payload["batch_id"]) == 64
    for field in ("generated_at", "run_date", "evaluation_date", "latest_trade_date", "summary", "market_regime", "realtime_scope", "snapshot_scope", "all_rows"):
        assert field in payload
    assert "secret" not in payload["all_rows"][0]


def test_batch_id_is_semantic_and_deterministic():
    source = {
        "generated_at": "first",
        "run_date": "2026-07-20", "evaluation_date": "2026-07-20", "latest_trade_date": "2026-07-20",
        "summary": {"universe_count": 1}, "market_regime": {}, "realtime_scope": [], "snapshot_scope": [],
        "all_rows": [{"code": "510300", "name": "沪深300ETF", "price": 4.1}],
    }
    first = module.build_payload(source)
    second = module.build_payload({**source, "generated_at": "second"})
    assert first["batch_id"] == second["batch_id"]
    changed = module.build_payload({**source, "all_rows": [{"code": "510300", "name": "沪深300ETF", "price": 4.2}]})
    assert changed["batch_id"] != first["batch_id"]
