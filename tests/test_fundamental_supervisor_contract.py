"""Additional private-namespace, offline supervisor contracts."""
import os
from pathlib import Path
import signal
import time

import pytest

from test_fundamental_partition_containment import isolated_offline, _reap_group
from scripts import a_share_fundamental_shadow as shadow


def _escape(marker):
    child = os.fork()
    if child == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        pending_marker = marker.with_suffix('.pending')
        pending_marker.write_text(str(os.getpid()))
        pending_marker.replace(marker)
        time.sleep(30)
        os._exit(0)
    deadline = time.monotonic() + .1
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.001)


@pytest.mark.parametrize('finish', ['success', 'exception', 'timeout'])
def test_owner_reaps_escaped_descendant_on_every_verdict(tmp_path, monkeypatch, finish):
    marker = tmp_path / 'descendant'
    def collect(*args):
        _escape(marker)
        if finish == 'timeout':
            time.sleep(30)
        if finish == 'exception':
            raise ValueError('provider failed')
        return [('A', 'succeeded', {'blob': 'x' * 200_000}, None)]
    monkeypatch.setattr(shadow, '_collect_universe', collect)
    state = tmp_path / 'state.json'
    try:
        if finish == 'success':
            result = shadow.collect_supervised([], 1, state)
            assert len(result[0][2]['blob']) == 200_000
        elif finish == 'exception':
            with pytest.raises(ValueError, match='provider failed'):
                shadow.collect_supervised([], 1, state)
        else:
            with pytest.raises(TimeoutError):
                shadow.collect_supervised([], 1, state)
        assert not Path('/proc/' + marker.read_text()).exists()
        assert not state.with_name(state.name + '.containment-fence').exists()
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_fence_retained_when_cleanup_proof_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, '_collect_universe', lambda *a: [])
    monkeypatch.setattr(shadow, '_reap_collection_tree', lambda: False)
    state = tmp_path / 'state.json'
    before = shadow._descendants(os.getpid())
    try:
        with pytest.raises(RuntimeError, match='cleanup unproved'):
            shadow.collect_supervised([], 1, state)
        fence = state.with_name(state.name + '.containment-fence')
        assert fence.exists()
        with pytest.raises(RuntimeError, match='containment fence'):
            shadow.collect_supervised([], 1, state)
    finally:
        for pid in shadow._descendants(os.getpid()) - before:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                os.waitpid(-1, 0)
            except ChildProcessError:
                break


def test_fence_retained_after_owner_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, '_collection_owner', lambda *a: os._exit(72))
    state = tmp_path / 'state.json'
    before = shadow._descendants(os.getpid())
    with pytest.raises(RuntimeError, match='cleanup unproved'):
        shadow.collect_supervised([], 1, state)
    assert state.with_name(state.name + '.containment-fence').exists()
    # The caller must still reap its exact direct child when proof is unavailable.
    assert shadow._descendants(os.getpid()) == before


def test_collection_retains_provider_streams(tmp_path, monkeypatch, capfd):
    import sys
    def collect(*a):
        print('provider stdout retained')
        print('provider stderr retained', file=sys.stderr)
        return []
    monkeypatch.setattr(shadow, '_collect_universe', collect)
    shadow.collect_supervised([], 1, tmp_path / 'state.json')
    output = capfd.readouterr()
    assert 'provider stdout retained' in output.out
    assert 'provider stderr retained' in output.err


def test_parent_death_keeps_owner_until_descendant_cleanup(tmp_path, monkeypatch):
    marker = tmp_path / 'escaped'
    monkeypatch.setattr(shadow, 'COLLECTION_TIMEOUT_S', 10)
    def collect(*a):
        _escape(marker)
        time.sleep(30)
    monkeypatch.setattr(shadow, '_collect_universe', collect)
    state = tmp_path / 'state.json'
    before = shadow._descendants(os.getpid())
    parent = os.fork()
    if parent == 0:
        os.setsid()
        shadow.collect_supervised([], 1, state)
        os._exit(0)
    try:
        deadline = time.monotonic() + 1
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert marker.exists()
        # Kill the caller's entire original group; the detached owner survives.
        os.killpg(parent, signal.SIGKILL)
        os.waitpid(parent, 0)
        while time.monotonic() < deadline:
            for child in shadow._descendants(os.getpid()) - before:
                try:
                    os.waitpid(child, os.WNOHANG)
                except ChildProcessError:
                    pass
            if shadow._descendants(os.getpid()) == before:
                break
            time.sleep(.005)
        assert not Path('/proc/' + marker.read_text()).exists()
        assert shadow._descendants(os.getpid()) == before
        assert state.with_name(state.name + '.containment-fence').exists()
    finally:
        for pid in shadow._descendants(os.getpid()) - before:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                os.waitpid(-1, 0)
            except ChildProcessError:
                break


@pytest.mark.parametrize('handoff', ['fence_release', 'handler_restore'])
def test_cancel_during_cleanup_handoff_blocks_writes(tmp_path, monkeypatch, handoff):
    monkeypatch.setattr(shadow, '_collect_universe', lambda *a: [])
    original_unlink = Path.unlink
    original_signal = signal.signal
    fired = False
    old_term = signal.getsignal(signal.SIGTERM)
    def unlink(path, *args, **kwargs):
        nonlocal fired
        result = original_unlink(path, *args, **kwargs)
        if handoff == 'fence_release' and path.name.endswith('.containment-fence'):
            fired = True
            os.kill(os.getpid(), signal.SIGTERM)
        return result
    def set_signal(sig, handler):
        nonlocal fired
        if handoff == 'handler_restore' and sig == signal.SIGTERM and handler == old_term:
            fired = True
            os.kill(os.getpid(), signal.SIGTERM)
        return original_signal(sig, handler)
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(signal, 'signal', set_signal)
    with pytest.raises(shadow.CollectionCancelled):
        shadow.collect_supervised([], 1, tmp_path / 'state.json')
    assert fired
    assert signal.getsignal(signal.SIGTERM) == old_term
    assert not (tmp_path / 'state.json.containment-fence').exists()


@pytest.mark.parametrize('workers', [1, 2])
def test_collection_success_writes_only_after_cleanup(tmp_path, monkeypatch, workers):
    marker = tmp_path / 'escaped'
    raw = {
        'stock_code': 'A', 'stock_name': 'offline',
        'valuation_rows': [
            ['2026-08-12', 'sh.A', '10', '1', '1', '1', '1'],
            ['2026-08-13', 'sh.A', '11', '1', '1', '1', '1']],
        'share_rows': [{'date': '2026-08-01', 'total_share': 10}],
    }
    def fetch(*a):
        _escape(marker)
        return [('A', 'succeeded', raw, None)]
    monkeypatch.setattr(shadow, '_fetch_partition', fetch)
    writes = []
    def write(*args):
        assert not Path('/proc/' + marker.read_text()).exists()
        assert not (tmp_path / 'state.json.containment-fence').exists()
        writes.append(args)
    monkeypatch.setattr(shadow, 'post_metrics', lambda *a: (write('D1'), 1)[1])
    monkeypatch.setattr(shadow, 'atomic_write', write)
    result = shadow.run(workers=workers, write_d1=True, token='offline')
    assert result['production_role'] == 'audit_only'
    assert result['coverage']['publishable'] is True
    assert result['d1_inserted'] == 1
    assert len(writes) == 3
