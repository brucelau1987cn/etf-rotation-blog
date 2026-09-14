import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_us_compass_downstream_gate.py"
SPEC = importlib.util.spec_from_file_location("us_downstream_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
NY = ZoneInfo("America/New_York")
MONDAY_AFTER_CLOSE = datetime(2026, 9, 14, 6, 30, tzinfo=NY)


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def valid_tree(root: Path) -> tuple[Path, Path]:
    data = root / "public/data"
    state = root / "publisher.json"
    write(data / "us-etf-pool.json", {"model_date": "2026-09-11", "session_state": "closed"})
    write(data / "us-etf-garden.json", {"date": "2026-09-11", "stage": "美股收盘版", "session_state": "closed"})
    write(data / "us-etf-flower-history.json", {"history": [{"date": "2026-09-11"}]})
    write(state, {"phase": "published", "trade_date": "2026-09-11", "verified": True, "deployed": True})
    return data, state


def test_downstream_gate_accepts_verified_same_date_publication(tmp_path):
    data, state = valid_tree(tmp_path)
    result = gate.evaluate(data_dir=data, state_path=state, require_history=True, now=MONDAY_AFTER_CLOSE)
    assert result["decision"] == "run"
    assert result["trade_date"] == "2026-09-11"


def test_downstream_gate_blocks_unpublished_state(tmp_path):
    data, state = valid_tree(tmp_path)
    write(state, {"phase": "generating", "trade_date": "2026-09-11"})
    result = gate.evaluate(data_dir=data, state_path=state, require_history=True, now=MONDAY_AFTER_CLOSE)
    assert result["decision"] == "blocked"
    assert "publisher state" in result["reason"]


def test_downstream_gate_blocks_cross_date_history(tmp_path):
    data, state = valid_tree(tmp_path)
    write(data / "us-etf-flower-history.json", {"history": [{"date": "2026-09-10"}]})
    result = gate.evaluate(data_dir=data, state_path=state, require_history=True, now=MONDAY_AFTER_CLOSE)
    assert result["decision"] == "blocked"
    assert "history" in result["reason"]


def test_downstream_gate_accepts_verified_idempotent_state(tmp_path):
    data, state = valid_tree(tmp_path)
    write(state, {"phase": "idempotent", "trade_date": "2026-09-11", "verified": True, "deployed": True})
    assert gate.evaluate(data_dir=data, state_path=state, now=MONDAY_AFTER_CLOSE)["decision"] == "run"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 7, 6, 5, 59, tzinfo=NY), "2026-07-02"),  # weekend + Independence Day observed
        (datetime(2026, 7, 6, 6, 30, tzinfo=NY), "2026-07-02"),
        (datetime(2026, 11, 27, 12, 59, tzinfo=NY), "2026-11-25"),  # Thanksgiving then early close
        (datetime(2026, 11, 27, 13, 0, tzinfo=NY), "2026-11-27"),
        (datetime(2026, 3, 9, 15, 59, tzinfo=NY), "2026-03-06"),  # EDT after DST starts
        (datetime(2026, 3, 9, 16, 0, tzinfo=NY), "2026-03-09"),
        (datetime(2026, 11, 2, 15, 59, tzinfo=NY), "2026-10-30"),  # EST after DST ends
        (datetime(2026, 11, 2, 16, 0, tzinfo=NY), "2026-11-02"),
    ],
)
def test_expected_completed_trade_date_uses_xnys_schedule(now, expected):
    assert gate.expected_completed_trade_date(now) == expected


def test_downstream_gate_blocks_stale_verified_release_after_cutoff(tmp_path):
    data, state = valid_tree(tmp_path)
    write(data / "us-etf-pool.json", {"model_date": "2026-09-10", "session_state": "closed"})
    write(data / "us-etf-garden.json", {"date": "2026-09-10", "stage": "美股收盘版", "session_state": "closed"})
    write(state, {"phase": "published", "trade_date": "2026-09-10", "verified": True, "deployed": True})
    result = gate.evaluate(
        data_dir=data,
        state_path=state,
        now=datetime(2026, 9, 14, 6, 30, tzinfo=NY),
    )
    assert result["decision"] == "blocked"
    assert result["expected_trade_date"] == "2026-09-11"


def test_downstream_gate_allows_previous_close_before_cutoff(tmp_path):
    data, state = valid_tree(tmp_path)
    result = gate.evaluate(
        data_dir=data,
        state_path=state,
        now=datetime(2026, 9, 14, 5, 59, tzinfo=NY),
    )
    assert result["decision"] == "run"
    assert result["expected_trade_date"] == "2026-09-11"


def test_downstream_gate_blocks_this_run_upstream_failure_after_cutoff(tmp_path):
    data, state = valid_tree(tmp_path)
    write(
        state,
        {
            "phase": "error",
            "trade_date": "2026-09-11",
            "verified": True,
            "deployed": True,
            "updated_at": "2026-09-14T10:31:00+00:00",
            "error": "upstream failed",
        },
    )
    result = gate.evaluate(
        data_dir=data,
        state_path=state,
        now=datetime(2026, 9, 14, 6, 31, tzinfo=NY),
    )
    assert result["decision"] == "blocked"
    assert "upstream" in result["reason"]
