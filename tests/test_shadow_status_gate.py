import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/shadow_status_gate.py"
SPEC = importlib.util.spec_from_file_location("shadow_status_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_degraded_alerts_after_three_consecutive_runs(tmp_path):
    state = tmp_path / "state.json"
    first = gate.record_status(state, "degraded", threshold=3)
    second = gate.record_status(state, "degraded", threshold=3)
    third = gate.record_status(state, "degraded", threshold=3)
    assert [first["exit_code"], second["exit_code"], third["exit_code"]] == [0, 0, 1]
    assert third["streak"] == 3
    assert json.loads(state.read_text())["status"] == "degraded"


def test_error_alerts_immediately_and_ok_resets_streak(tmp_path):
    state = tmp_path / "state.json"
    assert gate.record_status(state, "error")["exit_code"] == 1
    result = gate.record_status(state, "ok")
    assert result == {"status": "ok", "streak": 0, "alert": False, "exit_code": 0}


def test_stale_input_uses_consecutive_abnormal_gate(tmp_path):
    state = tmp_path / "state.json"
    first = gate.record_status(state, "stale_input", threshold=3)
    second = gate.record_status(state, "stale_input", threshold=3)
    third = gate.record_status(state, "stale_input", threshold=3)
    assert [first["exit_code"], second["exit_code"], third["exit_code"]] == [0, 0, 1]
    assert third["streak"] == 3


def test_calendar_skip_resets_abnormal_streak(tmp_path):
    state = tmp_path / "state.json"
    gate.record_status(state, "stale_input", threshold=3)
    result = gate.record_status(state, "skipped_closed", threshold=3)
    assert result == {"status": "skipped_closed", "streak": 0, "alert": False, "exit_code": 0}