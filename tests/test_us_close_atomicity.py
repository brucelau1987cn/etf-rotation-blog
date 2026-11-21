"""Independent spec probes; scratch-only Git remotes and mocked external I/O."""
import json
from contextlib import nullcontext
import io
from pathlib import Path
import runpy
import sys

import pytest

from test_us_close_entry import close, data, git, OLD, DAY, pub


def advance_remote(tmp_path, bare):
    other = tmp_path / 'other'
    git(tmp_path, 'clone', '-q', str(bare), str(other))
    git(other, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-qm', 'another release')
    git(other, 'push', 'origin', 'main')
    return git(other, 'rev-parse', 'HEAD')


def test_interrupted_push_with_advanced_remote_blocks_before_ff(close, monkeypatch, tmp_path):
    root, bare, _, state, drafts, events, _ = close
    orig = pub.write_state
    with monkeypatch.context() as m:
        def interrupt(phase, **kw):
            if phase == 'pushed':
                raise KeyboardInterrupt('push succeeded, receipt interrupted')
            orig(phase, **kw)
        m.setattr(pub, 'write_state', interrupt)
        with pytest.raises(KeyboardInterrupt):
            pub.main()
    sha = git(root, 'rev-parse', 'HEAD')
    assert sha == git(bare, 'rev-parse', 'HEAD')
    assert json.loads(state.read_bytes())['pushed'] is False
    assert advance_remote(tmp_path, bare) != sha
    before = state.read_bytes()
    events.clear()
    yahoo_calls = []
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: yahoo_calls.append(DAY) or DAY)
    with pytest.raises(RuntimeError):
        pub.main()
    assert (git(root, 'rev-parse', 'HEAD'), yahoo_calls) == (sha, [])
    assert state.read_bytes() == before and not events
    assert all((root / p).read_text() == raw for p, raw in drafts.items())


def test_scheduled_same_date_rejection_preserves_original_bytes(close, monkeypatch):
    root, bare, baseline, state, _, events, _ = close
    before = ('{\n  "phase": "error", "trade_date": "' + OLD + '", "commit": "' + baseline +
              '", "deployed": true, "verified": true\n}\n').encode()
    state.write_bytes(before)
    import a_share_nightly_contract
    entry = next(i for i, line in enumerate(Path(pub.__file__).read_text().splitlines(), 1)
                 if line == 'if __name__ == "__main__":')
    monkeypatch.setattr(a_share_nightly_contract, 'site_publish_lock', nullcontext)
    def trace(frame, event, arg):
        if event == 'line' and frame.f_globals.get('__name__') == '__main__' and frame.f_code.co_filename == pub.__file__:
            if frame.f_lineno == entry:
                for key in ('REPO', 'STATE', 'POOL', 'GARDEN', 'run', 'session_state'):
                    frame.f_globals[key] = getattr(pub, key)
                frame.f_globals['latest_spy_date'] = lambda: OLD
        return trace
    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        with pytest.raises(RuntimeError, match='same-date.*unverified'):
            runpy.run_path(pub.__file__, run_name='__main__')
    finally:
        sys.settrace(previous)
    assert state.read_bytes() == before
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') == baseline and not events


def test_remote_drift_during_readback_blocks_published(close, tmp_path):
    root, bare, _, state, _, events, faults = close
    faults['readback'] = lambda: advance_remote(tmp_path, bare)
    with pytest.raises(RuntimeError, match='remote main drifted during production readback'):
        pub.main()
    receipt = json.loads(state.read_bytes())
    assert receipt['commit'] == git(root, 'rev-parse', 'HEAD') != git(bare, 'rev-parse', 'HEAD')
    assert (receipt['pushed'], receipt['deployed'], receipt['verified']) == (True, True, False)
    assert events[-1][0] == 'readback'


def test_dist_drift_before_readback_blocks_success(close, monkeypatch):
    root, bare, _, state, _, events, _ = close
    pages = pub.pages_release
    clean_purge = pages.purge_custom_domain
    probed = []
    def purge_then_drift():
        clean_purge()
        tree = next(item[1] for item in events if item[0] == 'build')
        (tree / 'dist/data' / Path(pub.US_OWNED_FILES[0]).name).write_bytes(b'{"new":true}')
    def http_readback(request, **_):
        url = request.full_url.split('?', 1)[0]
        tree = next(item[1] for item in events if item[0] == 'build')
        raw = (tree / 'dist/data' / url.rsplit('/', 1)[-1]).read_bytes()
        probed.append((url, raw))
        return io.BytesIO(raw)
    monkeypatch.setattr(pages, 'purge_custom_domain', purge_then_drift)
    # Restore the real comparator; mock only HTTP transport.
    monkeypatch.setattr(pages, 'probe_json_bytes', lambda matches: pages._probe_json(matches, exact=True))
    monkeypatch.setattr(pages.urllib.request, 'urlopen', http_readback)
    monkeypatch.setattr(pages, 'JSON_PROBE_ATTEMPTS', 1)
    with pytest.raises(RuntimeError, match='production JSON differs'):
        pub.main()
    assert probed == [(f'{pub.SITE}/data/us-etf-pool.json', b'{"new":true}')]
    receipt = json.loads(state.read_bytes())
    assert receipt['pushed'] is receipt['deployed'] is True and receipt['verified'] is False
    assert receipt['verified_assets'] is None


def test_same_date_repair_uses_owned_close_without_collecting(close, monkeypatch):
    root, bare, _, state, _, events, faults = close
    path = root / pub.FILES[0]
    path.write_text(json.dumps({**json.loads(path.read_text()), 'repair': True}))
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: OLD)
    faults['collect'] = lambda: pytest.fail('same-date repair collected bars')
    with pytest.raises(SystemExit) as done:
        pub.main()
    assert done.value.code == 0
    receipt = json.loads(state.read_bytes())
    sha = git(bare, 'rev-parse', 'HEAD')
    assert receipt['trade_date'] == OLD and receipt['commit'] == sha and receipt['verified'] is True
    assert [e[0] for e in events] == ['build', 'push', 'deploy', 'deployment evidence', 'purge', 'urls', 'readback']


def test_same_date_noop_rechecks_index_after_remote_io(close, monkeypatch):
    root, _, _, state, _, events, _ = close
    pub.main()
    before = state.read_bytes()
    events.clear()
    remote_main = pub.remote_main

    def remote_then_stage():
        sha = remote_main()
        git(root, 'add', 'scripts/work.py')
        return sha

    monkeypatch.setattr(pub, 'remote_main', remote_then_stage)
    with pytest.raises(RuntimeError, match='index|staged'):
        pub.main()
    assert state.read_bytes() == before and not events
