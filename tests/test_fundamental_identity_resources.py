"""Offline private-namespace PID identity and admission resource regressions."""
import os
from pathlib import Path
import signal
import time

import pytest

from test_fundamental_partition_containment import isolated_offline
from scripts import a_share_fundamental_shadow as shadow


@pytest.mark.parametrize('race', ['stale_snapshot', 'acquisition_reuse'])
def test_cleanup_preserves_unrelated_sibling(monkeypatch, race):
    victim = os.fork()
    if victim == 0:
        time.sleep(30)
        os._exit(0)
    owner = os.fork()
    if owner == 0:
        owned = os.fork()
        if owned == 0:
            time.sleep(30)
            os._exit(0)
        monkeypatch.setattr(shadow, '_descendants', lambda _: {owned, victim})
        # Emulate a process that belonged to this owner immediately before
        # pidfd acquisition; the post-acquisition stat exposes the reused PID.
        if race == 'acquisition_reuse':
            real_read = Path.read_text
            real_open = os.pidfd_open
            acquired = False
            def read(path, *args, **kwargs):
                text = real_read(path, *args, **kwargs)
                if str(path) == f'/proc/{victim}/stat' and not acquired:
                    head, tail = text.rsplit(')', 1)
                    fields = tail.split()
                    fields[1] = str(os.getpid())
                    return head + ') ' + ' '.join(fields)
                return text
            def acquire(pid, flags=0):
                nonlocal acquired
                fd = real_open(pid, flags)
                if pid == victim:
                    acquired = True
                return fd
            monkeypatch.setattr(Path, 'read_text', read)
            monkeypatch.setattr(os, 'pidfd_open', acquire)
        try:
            proved = shadow._reap_collection_tree()
            os._exit(0 if proved else 10)
        except BaseException:
            os._exit(11)
    try:
        _, status = os.waitpid(owner, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        done, status = os.waitpid(victim, os.WNOHANG)
        assert done == 0, f'unrelated sibling signalled: {os.waitstatus_to_exitcode(status)}'
    finally:
        try:
            os.kill(victim, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(victim, 0)
        except ChildProcessError:
            pass


@pytest.mark.parametrize('failure', ['fdopen', 'file_fsync', 'directory_fsync', 'first_pipe', 'second_pipe'])
def test_admission_failure_closes_resources_retains_fence(tmp_path, monkeypatch, failure):
    state = tmp_path / 'state.json'
    before = set(os.listdir('/proc/self/fd'))
    attribute = 'fdopen' if failure == 'fdopen' else ('fsync' if 'fsync' in failure else 'pipe')
    real = getattr(os, attribute)
    fail_at = 2 if failure in ('directory_fsync', 'second_pipe') else 1
    calls = 0
    def fail(*args):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError('injected admission failure')
        return real(*args)
    monkeypatch.setattr(os, attribute, fail)
    monkeypatch.setattr(os, 'fork', lambda: pytest.fail('owner launched after failed admission'))
    try:
        with pytest.raises(OSError, match='injected admission failure'):
            shadow.collect_supervised([], 1, state)
        assert state.with_name(state.name + '.containment-fence').exists()
        leaked = set(os.listdir('/proc/self/fd')) - before
        assert leaked == set(), f'leaked descriptors: {leaked}'
    finally:
        # Keep RED fault injection isolated from subsequent tests.
        for fd in set(os.listdir('/proc/self/fd')) - before:
            try:
                os.close(int(fd))
            except OSError:
                pass
