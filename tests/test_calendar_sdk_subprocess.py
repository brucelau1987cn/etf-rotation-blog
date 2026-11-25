"""Offline calendar fallback regression: no SDK/network is invoked."""
import builtins
import json
import subprocess
import pytest

@pytest.fixture(autouse=True)
def block_in_process_sdk(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "baostock":
            raise ModuleNotFoundError("SDK blocked in offline suite")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)

from types import SimpleNamespace

from scripts import check_a_share_cron_gate as gate


@pytest.mark.parametrize("value", [True, False])
def test_sdk_uses_dependency_complete_bounded_child(monkeypatch, value):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.baostock_trading_day("2026-11-24") is value
    command, options = calls[0]
    assert command[0] == "/usr/bin/python3"
    assert command[-1] == "2026-11-24"
    assert 0 < options["timeout"] <= 10
    assert options["cwd"] == gate.ROOT


@pytest.mark.parametrize("payload", ["null", '"false"', "0", "{}", "garbage"])
def test_sdk_child_unknown_stays_unknown(monkeypatch, payload):
    monkeypatch.setattr(gate, "_backoff", lambda attempt: None)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload))
    assert gate.baostock_trading_day("2026-11-24") is None


def test_sdk_timeout_stays_fail_closed(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(gate, "_backoff", lambda attempt: None)
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.baostock_trading_day("2026-11-24") is None
    assert calls == [10, 10]


@pytest.mark.parametrize("row, expected", [
    (["2026-11-24", "1"], True),
    (["2026-11-24", "0"], False),
    (["2026-11-23", "0"], None),
    (["2026-11-24", "unknown"], None),
    (["2026-11-24"], None),
])
def test_sdk_row_requires_exact_date_and_explicit_flag(monkeypatch, row, expected):
    calls = []
    result = SimpleNamespace(error_code="0", data=[row], next=lambda: True, get_row_data=lambda: row)
    sdk = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0"),
        query_trade_dates=lambda **kw: result,
        logout=lambda: calls.append("logout"),
    )
    original = builtins.__import__
    def fake_import(name, *a, **kw):
        return sdk if name == "baostock" else original(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert gate._baostock_sdk_day("2026-11-24") is expected
    assert calls == ["logout"]


def test_sdk_rejects_multirow_single_day_response(monkeypatch):
    rows = [["2026-11-24", "1"], ["2026-11-24", "0"]]
    result = SimpleNamespace(error_code="0", data=rows, next=lambda: True, get_row_data=lambda: rows[0])
    sdk = SimpleNamespace(login=lambda: SimpleNamespace(error_code="0"), query_trade_dates=lambda **kw: result, logout=lambda: None)
    original = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **kw: sdk if name == "baostock" else original(name, *a, **kw))
    assert gate._baostock_sdk_day("2026-11-24") is None
