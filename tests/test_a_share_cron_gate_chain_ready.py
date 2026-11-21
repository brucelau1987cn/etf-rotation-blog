"""Offline regressions for durable nightly prerequisite evidence."""
import json
import socket

import pytest

from scripts import check_a_share_cron_gate as gate
from scripts import wait_a_share_nightly_state as state

DAY = "2026-11-20"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "NIGHTLY_STATUS", tmp_path / "trigger.json")
    monkeypatch.setattr(gate, "NIGHTLY_CHAIN_READY", tmp_path / "chain.json", raising=False)

    def forbid_network(*args, **kwargs):
        raise AssertionError("offline test attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)


def write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def ready_receipt():
    return {
        "version": 2,
        "requested_stage": "precheck-cache",
        "returncode": 0,
        "trade_date": DAY,
        "status": "ready",
        "ready_at": f"{DAY}T21:00:00+08:00",
        "results": [{"stage": stage, "ok": True} for stage in (
            "precheck", "cache", "fundamental-shadow",
        )],
        "fundamental": {
            "trade_date": DAY,
            "coverage": {"publishable": True, "failed": 0},
        },
    }


def test_complete_chain_survives_next_day_standalone_precheck():
    write(gate.NIGHTLY_CHAIN_READY, ready_receipt())
    write(gate.NIGHTLY_STATUS, {
        "requested_stage": "precheck", "ok": True, "status": "completed",
        "finished_at": "2026-11-21T10:00:00+08:00",
    })
    before = {path: path.read_bytes() for path in (
        gate.NIGHTLY_STATUS, gate.NIGHTLY_CHAIN_READY,
    )}

    assert gate.nightly_precheck_ready(DAY) is True
    assert gate.nightly_precheck_ready("2026-11-21") is False
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("trigger", [None, {"status": "running", "ok": False}])
def test_complete_chain_is_independent_of_trigger_availability(trigger):
    write(gate.NIGHTLY_CHAIN_READY, ready_receipt())
    if trigger is not None:
        write(gate.NIGHTLY_STATUS, trigger)
    assert gate.nightly_precheck_ready(DAY) is True


def test_chain_timestamp_is_evaluated_in_shanghai_timezone():
    payload = ready_receipt()
    payload["ready_at"] = "2026-11-19T16:01:00+00:00"
    write(gate.NIGHTLY_CHAIN_READY, payload)
    assert state.chain_ready(payload, DAY) is True
    assert gate.nightly_precheck_ready(DAY) is True


@pytest.mark.parametrize("mutation", [
    "duplicate-stage", "replaced-duplicate-stage", "extra-null-result", "null-result", "unhashable-stage",
    "bool-returncode", "float-returncode", "bool-failed", "float-failed",
    "float-version", "invalid-time", "date-only-time", "naive-time",
    "mismatched-local-day", "non-dict-payload", "missing-failed",
    "missing-coverage", "false-publishable", "nonzero-failed", "failed-stage", "missing-stage",
    "extra-stage", "wrong-trade-date", "wrong-fundamental-date",
    "false-like-ok", "wrong-status", "wrong-requested-stage",
])
def test_malformed_chain_fails_closed_in_both_consumers(mutation):
    payload = ready_receipt()
    if mutation == "duplicate-stage":
        payload["results"].append({"stage": "cache", "ok": True})
    elif mutation == "replaced-duplicate-stage":
        payload["results"][0]["stage"] = "cache"
    elif mutation == "extra-null-result":
        payload["results"].append(None)
    elif mutation == "null-result":
        payload["results"][0] = None
    elif mutation == "unhashable-stage":
        payload["results"][0]["stage"] = []
    elif mutation == "bool-returncode":
        payload["returncode"] = False
    elif mutation == "float-returncode":
        payload["returncode"] = 0.0
    elif mutation == "bool-failed":
        payload["fundamental"]["coverage"]["failed"] = False
    elif mutation == "float-failed":
        payload["fundamental"]["coverage"]["failed"] = 0.0
    elif mutation == "float-version":
        payload["version"] = 2.0
    elif mutation == "invalid-time":
        payload["ready_at"] = f"{DAY}garbage"
    elif mutation == "date-only-time":
        payload["ready_at"] = DAY
    elif mutation == "naive-time":
        payload["ready_at"] = f"{DAY}T21:00:00"
    elif mutation == "mismatched-local-day":
        payload["ready_at"] = f"{DAY}T21:00:00-08:00"
    elif mutation == "non-dict-payload":
        payload = []
    elif mutation == "missing-failed":
        del payload["fundamental"]["coverage"]["failed"]
    elif mutation == "missing-coverage":
        del payload["fundamental"]["coverage"]
    elif mutation == "false-publishable":
        payload["fundamental"]["coverage"]["publishable"] = False
    elif mutation == "nonzero-failed":
        payload["fundamental"]["coverage"]["failed"] = 1
    elif mutation == "failed-stage":
        payload["results"][1]["ok"] = False
    elif mutation == "missing-stage":
        payload["results"].pop()
    elif mutation == "extra-stage":
        payload["results"].append({"stage": "prepare", "ok": True})
    elif mutation == "wrong-trade-date":
        payload["trade_date"] = "2026-11-19"
    elif mutation == "wrong-fundamental-date":
        payload["fundamental"]["trade_date"] = "2026-11-19"
    elif mutation == "false-like-ok":
        payload["results"][1]["ok"] = 1
    elif mutation == "wrong-status":
        payload["status"] = "running"
    elif mutation == "wrong-requested-stage":
        payload["requested_stage"] = "precheck"
    write(gate.NIGHTLY_CHAIN_READY, payload)
    assert state.chain_ready(payload, DAY) is False
    assert gate.nightly_precheck_ready(DAY) is False


@pytest.mark.parametrize("payload", [[], None, True, "invalid"])
def test_malformed_legacy_status_fails_closed(payload):
    write(gate.NIGHTLY_STATUS, payload)
    assert gate.nightly_precheck_ready(DAY) is False


@pytest.mark.parametrize("raw", [b"{", b"\xff"])
def test_unreadable_receipts_fail_closed(raw):
    gate.NIGHTLY_CHAIN_READY.write_bytes(raw)
    gate.NIGHTLY_STATUS.write_bytes(raw)
    assert gate.nightly_precheck_ready(DAY) is False


@pytest.mark.parametrize("chain", [None, {}, {"trade_date": "2026-11-19"}])
def test_legacy_success_remains_compatible(chain):
    if chain is not None:
        write(gate.NIGHTLY_CHAIN_READY, chain)
    write(gate.NIGHTLY_STATUS, {
        "requested_stage": "precheck-cache", "ok": True,
        "finished_at": f"{DAY}T21:00:00+08:00",
    })
    assert gate.nightly_precheck_ready(DAY) is True


def test_gate_main_uses_chain_proof_with_mocked_files_and_providers(tmp_path, monkeypatch, capsys):
    import sys

    write(gate.NIGHTLY_CHAIN_READY, ready_receipt())
    write(gate.NIGHTLY_STATUS, {"requested_stage": "precheck", "ok": True})
    pool = tmp_path / "pool.json"
    write(pool, {"summary": {"universe_count": 91, "valid_count": 91}})
    monkeypatch.setattr(gate, "POOL", pool)
    monkeypatch.setattr(gate, "qfq_state", lambda: (DAY, 91))
    monkeypatch.setattr(gate, "quote_timestamp", lambda: "20261120150000")
    monkeypatch.setattr(gate, "read_article_stage", lambda day: "14:30尾盘操作版")
    monkeypatch.setattr(gate, "is_trading_day", lambda day: (True, "mock_calendar"))
    monkeypatch.setattr(gate, "pending_public_changes", lambda day: True)
    monkeypatch.setattr(sys, "argv", ["gate", "--stage", "22:00", "--now", f"{DAY}T22:00:00+08:00"])
    assert gate.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "run"
    assert result["nightly_precheck_ready"] is True
    gate.NIGHTLY_CHAIN_READY.unlink()
    assert gate.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "blocked"
    assert result["reason"] == "nightly precheck-cache not completed"
