import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.check_a_share_nightly_publish_gate import check_publish_gate


CN = ZoneInfo("Asia/Shanghai")
PUBLISH_WRAPPER = Path("/root/.hermes/scripts/publish_a_share_nightly.sh")


@pytest.mark.parametrize("status", [
    "content_ready", "candidate_validated", "committed", "deploy_failed", "published",
])
def test_publish_gate_accepts_handoff_and_recovery_manifests(tmp_path, status):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "version": 2,
        "status": status,
        "phase": status,
        "trade_date": "2026-07-14",
        "prepared_at": "2026-07-14T22:00:00+08:00",
    }))

    payload = check_publish_gate(state, datetime(2026, 7, 14, 22, 30, tzinfo=CN))

    assert payload["status"] == "ok"
    assert payload["manifest_status"] == status


def test_publish_gate_rejects_prepared_manifest(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "version": 2,
        "status": "prepared",
        "phase": "prepared",
        "trade_date": "2026-07-14",
        "prepared_at": "2026-07-14T22:00:00+08:00",
    }))

    with pytest.raises(RuntimeError, match="content_ready or a publisher recovery status"):
        check_publish_gate(state, datetime(2026, 7, 14, 22, 30, tzinfo=CN))


def test_publish_gate_rejects_stale_manifest(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "version": 2,
        "status": "content_ready",
        "phase": "content_ready",
        "trade_date": "2026-07-11",
        "prepared_at": "2026-07-11T22:00:00+08:00",
    }))

    with pytest.raises(RuntimeError, match="current date"):
        check_publish_gate(state, datetime(2026, 7, 14, 22, 30, tzinfo=CN))


def test_publish_wrapper_waits_for_content_handoff_before_publish_gate():
    wrapper = PUBLISH_WRAPPER.read_text(encoding="utf-8")
    wait = "wait_a_share_nightly_state.py --stage content"
    gate = "check_a_share_nightly_publish_gate.py"
    publish = "publish_a_share_nightly.py"
    assert wait in wrapper
    assert wrapper.index(wait) < wrapper.index(gate) < wrapper.index(publish)