"""Scheduled-entry regressions using disposable Git and simulated transports."""
from contextlib import nullcontext
import json
import subprocess
from pathlib import Path

import pytest

from test_us_close_atomicity import advance_remote
from test_us_close_entry import DAY, OLD, close, git, pub


PAGES = pub.pages_release
REAL_DEPLOY = PAGES.deploy_pages


@pytest.mark.parametrize('drift', ['remote', 'dist', 'source', 'index', 'candidate', 'first-proxy', None])
def test_each_upload_rechecks_after_retry_delay(close, monkeypatch, tmp_path, drift):
    root, bare, _, state, _, events, _ = close
    real_run = subprocess.run
    uploads = []

    def launch(args, **kwargs):
        if args[0] != 'npx':
            return real_run(args, **kwargs)
        uploads.append(args)
        if len(uploads) == 1:
            return subprocess.CompletedProcess(args, 1, '', 'transient transport failure')
        return subprocess.CompletedProcess(args, 0, 'Deployment complete', '')

    def backoff(_):
        tree = next(item[1] for item in events if item[0] == 'build')
        if drift == 'remote':
            advance_remote(tmp_path, bare)
        elif drift == 'dist':
            (tree / 'dist/data' / Path(pub.FILES[0]).name).write_bytes(b'wrong dist bytes')
        elif drift == 'source':
            (root / pub.FILES[0]).write_bytes(b'concurrent owned edit')
        elif drift == 'index':
            git(root, 'add', 'scripts/work.py')
        elif drift == 'candidate':
            (tree / pub.FILES[0]).write_bytes(b'wrong public bytes')

    def prepare_proxy(env):
        if drift == 'first-proxy':
            git(root, 'add', 'scripts/work.py')
        return env

    monkeypatch.setattr(PAGES, 'deploy_pages', REAL_DEPLOY)
    monkeypatch.setattr(PAGES, 'load_env_file', lambda _: {'CLOUDFLARE_API_TOKEN': 'fake-token'})
    monkeypatch.setattr(PAGES, 'proxy_env', prepare_proxy)
    monkeypatch.setattr(PAGES.time, 'sleep', backoff)
    monkeypatch.setattr(subprocess, 'run', launch)
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)

    if drift is None:
        pub.main()
        assert len(uploads) == 2
        assert [item[0] for item in events][-4:] == ['deployment evidence', 'purge', 'urls', 'readback']
    else:
        pushed = []
        real_write = pub.write_state

        def remember_push(phase, **fields):
            real_write(phase, **fields)
            if phase == 'pushed':
                pushed.append(state.read_bytes())

        monkeypatch.setattr(pub, 'write_state', remember_push)
        # On the original entry, remote drift can be detected by the simulated
        # deployment evidence hook; the upload count verifies the actual gate.
        with pytest.raises((RuntimeError, AssertionError)) as blocked:
            pub.cli()
        assert len(uploads) == (0 if drift == 'first-proxy' else 1)
        assert isinstance(blocked.value, RuntimeError)
        assert any(cause in str(blocked.value) for cause in (
            'remote main', 'dist bytes', 'owned output', 'source index', 'checkout changed'))
        assert [item[0] for item in events] == ['build', 'push']
        assert pushed == [state.read_bytes()]
        assert json.loads(state.read_bytes())['phase'] == 'pushed'
        assert str(blocked.value) in state.with_suffix('.error.json').read_text()
    sha = git(root, 'rev-parse', 'HEAD')
    assert all(args[args.index('--commit-hash') + 1] == sha for args in uploads)


def test_dist_drift_during_pre_push_remote_query_blocks_push(close, monkeypatch):
    root, bare, baseline, _, _, events, _ = close
    real_remote = pub.remote_main

    def query_then_drift():
        sha = real_remote()
        tree = next(item[1] for item in events if item[0] == 'build')
        (tree / 'dist/data' / Path(pub.US_OWNED_FILES[0]).name).write_bytes(b'wrong artifact')
        return sha

    monkeypatch.setattr(pub, 'remote_main', query_then_drift)
    with pytest.raises(RuntimeError, match='dist bytes differ'):
        pub.main()
    assert [item[0] for item in events] == ['build']
    assert git(bare, 'rev-parse', 'HEAD') == baseline != git(root, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('pending', [False, True])
def test_scheduled_preflight_failure_preserves_original_receipt(close, monkeypatch, pending):
    root, bare, _, state, _, events, _ = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    if pending:
        write_state = pub.write_state
        with monkeypatch.context() as interrupted:
            def stop_before_push_ack(phase, **fields):
                if phase == 'pushed':
                    raise KeyboardInterrupt
                write_state(phase, **fields)

            interrupted.setattr(pub, 'write_state', stop_before_push_ack)
            with pytest.raises(KeyboardInterrupt):
                pub.cli()
    else:
        pub.cli()
    before = state.read_bytes()
    sha = git(root, 'rev-parse', 'HEAD')
    events.clear()
    # The scheduler sees this error before the same-date/pending routing guard.
    git(root, 'add', 'scripts/work.py')
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: pytest.fail('unexpected Yahoo query'))
    with pytest.raises(RuntimeError, match='source index'):
        pub.cli()
    assert state.read_bytes() == before
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') == sha
    assert not events
    diagnostic = json.loads(state.with_suffix('.error.json').read_bytes())
    assert 'source index' in diagnostic['error']


def test_scheduled_failed_resume_keeps_deployed_evidence(close, monkeypatch):
    _, _, _, state, _, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)

    def fail_readback():
        assert json.loads(state.read_bytes())['phase'] == 'deployed'
        raise RuntimeError('readback unavailable')

    faults['readback'] = fail_readback
    with pytest.raises(RuntimeError, match='readback unavailable'):
        pub.main()
    before = state.read_bytes()
    events.clear()

    def fail_build(_):
        raise RuntimeError('candidate build unavailable')

    faults['build'] = fail_build
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: pytest.fail('unexpected Yahoo query'))
    with pytest.raises(RuntimeError, match='candidate build unavailable'):
        pub.cli()
    assert state.read_bytes() == before
    assert [item[0] for item in events] == ['build']
    assert 'candidate build unavailable' in state.with_suffix('.error.json').read_text()


@pytest.mark.parametrize('drift', ['clean', 'existing-draft', 'hidden', 'same-bytes', 'deleted'])
def test_owned_change_during_yahoo_blocks_repair(close, monkeypatch, drift):
    root, bare, baseline, state, _, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    target = root / pub.US_OWNED_FILES[-1]
    original = target.read_bytes()
    if drift == 'existing-draft':
        target.write_bytes(original + b'\n')
    if drift == 'hidden':
        git(root, 'update-index', '--assume-unchanged', pub.US_OWNED_FILES[-1])
    changed = original if drift == 'same-bytes' else original + b'\n\n'

    def query():
        if drift == 'deleted':
            target.unlink()
        elif drift == 'same-bytes':
            replacement = target.with_suffix('.new')
            replacement.write_bytes(changed)
            replacement.replace(target)
        else:
            target.write_bytes(changed)
        return DAY

    monkeypatch.setattr(pub, 'latest_spy_date', query)
    monkeypatch.setattr(pub, 'session_state', lambda *_: 'open')
    faults['collect'] = lambda: pytest.fail('owned drift triggered collection')
    before = state.read_bytes()
    with pytest.raises(RuntimeError, match='owned.*changed'):
        pub.cli()
    assert state.read_bytes() == before
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') == baseline
    assert not events
    assert (target.read_bytes() if target.exists() else None) == (None if drift == 'deleted' else changed)


def test_preexisting_close_draft_change_during_validation_blocks_commit(close, monkeypatch):
    root, bare, baseline, state, _, events, _ = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: OLD)
    target = root / pub.US_OWNED_FILES[-1]
    target.write_bytes(target.read_bytes() + b'\n')
    changed = target.read_bytes() + b'\n'
    validate = pub.validate_us_release_data

    def validate_then_drift(*args, **kwargs):
        validate(*args, **kwargs)
        if not args:
            target.write_bytes(changed)

    monkeypatch.setattr(pub, 'validate_us_release_data', validate_then_drift)
    before = state.read_bytes()
    with pytest.raises(RuntimeError, match='owned.*changed'):
        pub.cli()
    assert state.read_bytes() == before and target.read_bytes() == changed
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') == baseline
    assert not events


@pytest.mark.parametrize('stage', ['collect', 'retry-before-push'])
def test_scheduled_failure_without_new_phase_keeps_receipt(close, monkeypatch, stage):
    root, bare, baseline, state, _, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)

    def unavailable(*_):
        raise RuntimeError('stage unavailable')

    if stage == 'collect':
        faults['collect'] = unavailable
    else:
        faults['build'] = unavailable
        with pytest.raises(RuntimeError, match='stage unavailable'):
            pub.cli()
        assert json.loads(state.read_bytes())['phase'] == 'committed'
        monkeypatch.setattr(pub, 'latest_spy_date', lambda: pytest.fail('unexpected Yahoo query'))
    before = state.read_bytes()
    sha = git(root, 'rev-parse', 'HEAD')
    events.clear()
    with pytest.raises(RuntimeError, match='stage unavailable'):
        pub.cli()
    assert state.read_bytes() == before
    assert git(root, 'rev-parse', 'HEAD') == sha
    assert git(bare, 'rev-parse', 'HEAD') == baseline
    assert [item[0] for item in events] == ([] if stage == 'collect' else ['build'])
