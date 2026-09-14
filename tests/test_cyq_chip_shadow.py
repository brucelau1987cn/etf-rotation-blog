import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cyq_chip_shadow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cyq_chip_shadow_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_data_as_of_gate_writes_stale_status_and_returns_shadow_exit(tmp_path):
    mod = load_module()
    output = tmp_path / "cyq.json"
    result = mod.write_stale_input(
        output,
        actual="2026-09-11",
        expected="2026-09-14",
        generated_at="2026-09-14T16:45:00+08:00",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 2
    assert payload["status"] == "stale_input"
    assert payload["data_as_of"] == "2026-09-11"
    assert payload["expected_data_as_of"] == "2026-09-14"
    assert payload["production_effect"] == "none"


def test_calendar_gate_skips_closed_day_before_freshness_check():
    mod = load_module()
    assert mod.shadow_calendar_gate("2026-09-13", lookup=lambda day: (False, "fixture")) == {
        "status": "skip", "trade_date": "2026-09-13", "calendar_source": "fixture",
    }
    assert mod.shadow_calendar_gate("2026-09-14", lookup=lambda day: (True, "fixture"))["status"] == "run"