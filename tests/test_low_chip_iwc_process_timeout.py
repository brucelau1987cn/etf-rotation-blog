"""Fully mocked process-tree timeout regression tests; never launch the wrapper."""
import importlib.util
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "low_chip_iwc_timeout_test", ROOT / "scripts/fetch_low_chip_enrichments.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Both APIs are blocked until a test explicitly supplies its fake.
    def blocked(*args, **kwargs):
        pytest.fail("unexpected subprocess launch")
    monkeypatch.setattr(module.subprocess, "run", blocked)
    monkeypatch.setattr(module.subprocess, "Popen", blocked)
    return module


@pytest.mark.parametrize("term_completes", [False, True])
def test_timeout_terminates_then_kills_entire_group_and_reaps(mod, monkeypatch, term_completes):
    events = []
    expired = subprocess.TimeoutExpired("iwencai-market-query", 25)

    class Process:
        pid = 12345
        returncode = None

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if len([event for event in events if event[0] == "communicate"]) == 1:
                raise expired
            if timeout is not None and not term_completes:
                raise subprocess.TimeoutExpired("iwencai-market-query", timeout)
            self.returncode = -signal.SIGKILL
            return "", ""

    def popen(command, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["text"] is True
        return Process()

    # The original implementation must fail safely without spawning anything.
    def old_run(*args, **kwargs):
        raise expired

    monkeypatch.setattr(mod.subprocess, "run", old_run)
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mod.os, "killpg", lambda pid, sig: events.append(("killpg", pid, sig)))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        mod.iwc("offline timeout test", timeout=20, attempts=1)
    assert caught.value is expired
    assert events == [
        ("communicate", 25),
        ("killpg", 12345, signal.SIGTERM),
        ("communicate", 1),
        ("killpg", 12345, signal.SIGKILL),
        ("communicate", None),
    ]


@pytest.mark.parametrize("quota", [False, True])
def test_completed_process_preserves_json_and_quota_behavior(mod, monkeypatch, quota):
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        def communicate(timeout=None):
            assert timeout == 25
            return ("额度已用完", "") if quota else ('{"datas": []}', "")
        return SimpleNamespace(args=command, returncode=int(quota), communicate=communicate)

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    if quota:
        with pytest.raises(SystemExit, match="quota exhausted"):
            mod.iwc("offline query", timeout=20)
    else:
        assert mod.iwc("offline query", timeout=20) == {"datas": []}
    assert len(calls) == 1
    assert calls[0][1]["start_new_session"] is True


def test_timeout_tolerates_group_already_exited_and_still_reaps(mod, monkeypatch):
    deadlines = []
    signals = []
    expired = subprocess.TimeoutExpired("iwencai-market-query", 25)

    def communicate(timeout=None):
        deadlines.append(timeout)
        if len(deadlines) == 1:
            raise expired
        return "", ""

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
        pid=12345, communicate=communicate
    ))

    def vanished(pid, sig):
        signals.append(sig)
        raise ProcessLookupError

    monkeypatch.setattr(mod.os, "killpg", vanished)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        mod.iwc("offline query", timeout=20, attempts=1)
    assert caught.value is expired
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert deadlines == [25, 1, None]
