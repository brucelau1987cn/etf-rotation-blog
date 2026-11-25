"""Identity validation failures retain cleanup responsibility."""
import os
import signal
import time

import pytest

from test_fundamental_partition_containment import isolated_offline
from scripts import a_share_fundamental_shadow as shadow


@pytest.mark.parametrize('failure', ['pidfd_unavailable', 'stat_unavailable'])
def test_identity_uncertainty_preserves_live_child(monkeypatch, failure):
    child = os.fork()
    if child == 0:
        time.sleep(30)
        os._exit(0)
    def unavailable(*args, **kwargs):
        raise OSError('injected identity uncertainty')
    try:
        if failure == 'pidfd_unavailable':
            monkeypatch.setattr(os, 'pidfd_open', unavailable)
        else:
            monkeypatch.setattr(shadow, '_process_identity', unavailable)
        monkeypatch.setattr(shadow, 'CONTAINMENT_CLEANUP_S', .015)
        before = set(os.listdir('/proc/self/fd'))
        assert shadow._reap_collection_tree() is False
        assert os.waitpid(child, os.WNOHANG)[0] == 0
        assert set(os.listdir('/proc/self/fd')) == before
    finally:
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
