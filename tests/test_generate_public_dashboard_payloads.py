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
