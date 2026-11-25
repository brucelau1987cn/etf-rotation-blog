"""Offline public-path RED contract.

Run with unshare --pid --fork --kill-child --mount-proc --net.
The private PID namespace is the test watchdog's final containment boundary.
"""
import ctypes
import os
from pathlib import Path
import signal
import sys
import time
import types

import pytest
from scripts import a_share_fundamental_shadow as shadow


def _reap_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            child, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if child == 0:
            time.sleep(.005)


@pytest.fixture(autouse=True)
def isolated_offline(monkeypatch, tmp_path):
    if os.getpid() != 1:
        pytest.skip('requires dedicated unshare --pid --fork --mount-proc --net invocation')
    # Adopt grandchildren during watchdog teardown, including escaped sessions.
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    import socket
    def network_forbidden(*args, **kwargs):
        pytest.fail('real network access forbidden')
    monkeypatch.setattr(socket.socket, 'connect', network_forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', network_forbidden)
    monkeypatch.setattr(shadow, 'post_metrics', lambda *a: pytest.fail('D1 write forbidden'))
    monkeypatch.setattr(shadow, 'atomic_write', lambda *a: pytest.fail('artifact write forbidden'))
    monkeypatch.setattr(shadow, 'load_universe', lambda _: [{'code': 'A', 'market': 'SH'}])
    original_run = shadow.run
    def offline_run(**kwargs):
        kwargs.setdefault('state_path', tmp_path / 'state.json')
        kwargs.setdefault('output_path', tmp_path / 'output.json')
        return original_run(**kwargs)
    monkeypatch.setattr(shadow, 'run', offline_run)
    # Candidate should expose a short injected collection budget for offline tests.
    monkeypatch.setattr(shadow, 'COLLECTION_TIMEOUT_S', .2, raising=False)
    monkeypatch.setattr(shadow, 'CONTAINMENT_CLEANUP_S', .35, raising=False)


def _start_run():
    pid = os.fork()
    if pid == 0:
        os.setsid()
        try:
            shadow.run(workers=1, write_d1=True, token='offline-mock')
        except TimeoutError:
            os._exit(42)
        except BaseException:
            os._exit(43)
        os._exit(0)
    return pid


def _wait(pid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(.005)
    return None


@pytest.mark.parametrize('operation', ['login', 'query', 'logout'])
def test_public_collection_deadline_contains_session_operations(operation, monkeypatch):
    def hang():
        time.sleep(30)
    monkeypatch.setitem(sys.modules, 'baostock', types.SimpleNamespace(
        login=hang if operation == 'login' else lambda: types.SimpleNamespace(error_code='0'),
        logout=hang if operation == 'logout' else lambda: None,
    ))
    monkeypatch.setattr(shadow, '_fetch_symbol_with_session',
                        lambda *args: hang() if operation == 'query' else ('A', 'empty', None, None))
    pid = _start_run()
    try:
        result = _wait(pid, .8)
        assert result == 42, f'{operation} escaped collection deadline: watchdog result={result}'
    finally:
        _reap_group(pid)


def test_later_partition_exception_is_observed_before_blocked_first(monkeypatch):
    monkeypatch.setattr(shadow, 'load_universe', lambda _: [
        {'code': 'A', 'market': 'SH'}, {'code': 'B', 'market': 'SH'},
    ])
    def partition(items, sessions):
        if items[0]['code'] == 'A':
            time.sleep(30)
        raise RuntimeError('later partition terminal failure')
    monkeypatch.setattr(shadow, '_fetch_partition', partition)
    pid = os.fork()
    if pid == 0:
        os.setsid()
        try:
            shadow.run(workers=2, write_d1=True, token='offline-mock')
        except RuntimeError as exc:
            os._exit(44 if 'later partition terminal failure' in str(exc) else 43)
        except BaseException:
            os._exit(43)
        os._exit(0)
    try:
        assert _wait(pid, .8) == 44, 'later partition exception hidden behind blocked first/shutdown(wait=True)'
    finally:
        _reap_group(pid)


def test_retry_uses_same_collection_deadline(monkeypatch):
    calls = 0
    def partition(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [('A', 'failed', None, 'login failed')]
        time.sleep(30)
    monkeypatch.setattr(shadow, '_fetch_partition', partition)
    pid = _start_run()
    try:
        assert _wait(pid, .8) == 42, 'fresh-session retry escaped shared collection deadline'
    finally:
        _reap_group(pid)


@pytest.mark.parametrize('cancel_signal', [signal.SIGTERM, signal.SIGINT])
def test_public_cancellation_reaps_setsid_descendant(tmp_path, monkeypatch, cancel_signal):
    marker = tmp_path / 'escaped-pid'
    def partition(*args):
        child = os.fork()
        if child == 0:
            os.setsid()
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            pending_marker = marker.with_suffix('.pending')
            pending_marker.write_text(str(os.getpid()))
            pending_marker.replace(marker)
            time.sleep(30)
            os._exit(0)
        time.sleep(30)
    monkeypatch.setattr(shadow, '_fetch_partition', partition)
    pid = _start_run()
    escaped = None
    try:
        deadline = time.monotonic() + 1
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert marker.exists(), 'descendant capture barrier'
        escaped = int(marker.read_text())
        os.kill(pid, cancel_signal)
        result = _wait(pid, .8)
        assert result is not None and result != 0, 'cancellation must finish with failure'
        assert not Path(f'/proc/{escaped}').exists(), 'cancelled public collector left setsid descendant alive/unreaped'
    finally:
        if escaped is not None:
            try:
                os.kill(escaped, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _reap_group(pid)
