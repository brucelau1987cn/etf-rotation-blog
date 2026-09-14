import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import wait_a_share_nightly_state as gate


CN = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 14, 22, 0, tzinfo=CN)


def write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_chain_ready_survives_later_running_status(tmp_path):
    ready = tmp_path / "ready.json"
    status = tmp_path / "status.json"
    write(ready, {
        "version": 1,
        "trade_date": "2026-09-14",
        "status": "ready",
        "ready_at": "2026-09-14T22:04:47+08:00",
        "results": [{"stage": name, "ok": True} for name in (
            "precheck", "cache", "fundamental-shadow"
        )],
    })
    write(status, {
        "version": 1,
        "requested_stage": "precheck-cache",
        "status": "running",
        "ok": False,
    })

    result = gate.wait_for_stage(
        "chain", now=NOW, timeout=0, interval=0,
        ready_path=ready, trigger_path=status,
    )

    assert result["status"] == "ready"
    assert result["trade_date"] == "2026-09-14"


def test_chain_waits_through_running_then_reads_ready(tmp_path, monkeypatch):
    ready = tmp_path / "ready.json"
    status = tmp_path / "status.json"
    write(status, {"status": "running", "current_stage": "fundamental-shadow"})
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        write(ready, {
            "trade_date": "2026-09-14", "status": "ready",
            "ready_at": "2026-09-14T22:05:00+08:00",
            "results": [{"stage": name, "ok": True} for name in (
                "precheck", "cache", "fundamental-shadow"
            )],
        })

    monkeypatch.setattr(gate.time, "sleep", sleep)
    result = gate.wait_for_stage(
        "chain", now=NOW, timeout=10, interval=1,
        ready_path=ready, trigger_path=status,
    )

    assert result["status"] == "ready"
    assert sleeps == [1]


def test_content_waits_for_content_ready_after_chain_and_content_run_out_of_order(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    write(manifest, {
        "version": 2, "status": "prepared", "phase": "prepared",
        "trade_date": "2026-09-14", "prepared_at": "2026-09-14T22:05:00+08:00",
    })

    def sleep(_seconds):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload.update(status="content_ready", phase="content_ready")
        manifest.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(gate.time, "sleep", sleep)
    result = gate.wait_for_stage(
        "content", now=datetime(2026, 9, 14, 22, 30, tzinfo=CN),
        timeout=10, interval=1, manifest_path=manifest,
    )

    assert result["status"] == "content_ready"


def test_timeout_preserves_existing_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    original = {
        "version": 2, "status": "prepared", "phase": "prepared",
        "trade_date": "2026-09-14", "prepared_at": "2026-09-14T22:05:00+08:00",
    }
    write(manifest, original)

    with pytest.raises(TimeoutError, match="prepared"):
        gate.wait_for_stage(
            "content", now=NOW, timeout=0, interval=0, manifest_path=manifest,
        )

    assert json.loads(manifest.read_text(encoding="utf-8")) == original
