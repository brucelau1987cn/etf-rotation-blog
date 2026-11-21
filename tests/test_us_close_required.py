"""Required artifact boundaries through the scheduler, with scratch-only I/O."""
from contextlib import nullcontext
import json
import subprocess

import pytest

from test_us_close_atomicity import advance_remote
from test_us_close_entry import DAY, ROUTES, close, git, pub
from test_us_close_rechecks import REAL_DEPLOY


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_required_page_drift_during_pre_push_query_blocks_push(close, monkeypatch, route, mutation):
    root, bare, baseline, state, _, events, _ = close
    real_remote = pub.remote_main
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)

    def query_then_change_page():
        sha = real_remote()
        tree = next(item[1] for item in events if item[0] == 'build')
        page = tree / 'dist' / route / 'index.html'
        if mutation == 'missing':
            page.unlink()
        else:
            page.write_bytes(b'X' * page.stat().st_size)
        return sha

    monkeypatch.setattr(pub, 'remote_main', query_then_change_page)
    error = None
    try:
        pub.cli()
    except Exception as caught:
        error = caught
    assert git(bare, 'rev-parse', 'HEAD') == baseline != git(root, 'rev-parse', 'HEAD'), 'page drift escaped to push'
    assert [item[0] for item in events] == ['build']
    assert isinstance(error, RuntimeError) and f'{route}/index.html' in str(error)
    assert route + '/index.html' in state.with_suffix('.error.json').read_text()


@pytest.mark.parametrize('boundary', ['first-proxy', 'retry-delay', 'upload-remote'])
@pytest.mark.parametrize('mutation', ['missing', 'changed', 'symlink'])
def test_required_page_drift_blocks_each_upload(close, monkeypatch, boundary, mutation):
    root, bare, baseline, state, _, events, _ = close
    pages = pub.pages_release
    # The shared fixture replaces the transport wrapper, so restore its real loop.
    monkeypatch.setattr(pages, 'deploy_pages', REAL_DEPLOY)
    monkeypatch.setattr(pages, 'load_env_file', lambda _: {'CLOUDFLARE_API_TOKEN': 'fixture'})
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    commands = []
    pushed_receipt = []
    run = subprocess.run
    remote = pub.remote_main
    route = 'us-compass/history'

    def change_page():
        pushed_receipt.append(state.read_bytes())
        tree = events[0][1]
        page = tree / 'dist' / route / 'index.html'
        if mutation == 'missing':
            page.unlink()
        elif mutation == 'changed':
            page.write_bytes(b'X' * page.stat().st_size)
        else:
            # The target is inside the candidate and has identical bytes.
            page.unlink()
            page.symlink_to('../index.html')

    def launch(args, **kwargs):
        if args[0] != 'npx':
            return run(args, **kwargs)
        commands.append(args)
        if len(commands) == 1 and boundary == 'retry-delay':
            return subprocess.CompletedProcess(args, 1, '', 'fixture transport failure')
        return subprocess.CompletedProcess(args, 0, 'Deployment complete', '')

    def proxy(env):
        if boundary == 'first-proxy':
            change_page()
        return env

    def query():
        sha = remote()
        if boundary == 'upload-remote' and sha != baseline and not pushed_receipt:
            change_page()
        return sha

    monkeypatch.setattr(pages, 'proxy_env', proxy)
    monkeypatch.setattr(pages.time, 'sleep', lambda _: change_page())
    monkeypatch.setattr(pub, 'remote_main', query)
    monkeypatch.setattr(subprocess, 'run', launch)
    error = None
    try:
        pub.cli()
    except Exception as caught:
        error = caught
    assert len(commands) == (1 if boundary == 'retry-delay' else 0), 'page drift escaped to Wrangler'
    assert isinstance(error, RuntimeError) and f'{route}/index.html' in str(error)
    assert [item[0] for item in events] == ['build', 'push']
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') != baseline
    assert pushed_receipt == [state.read_bytes()]
    assert route + '/index.html' in state.with_suffix('.error.json').read_text()


@pytest.mark.parametrize('prior_phase', ['committed', 'error', 'pushed', 'deployed'])
def test_resume_build_failure_keeps_strongest_confirmed_receipt(close, monkeypatch, prior_phase):
    root, bare, baseline, state, drafts, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    real_write = pub.write_state

    def unavailable(*_):
        raise RuntimeError('fixture stage unavailable')

    if prior_phase in ('committed', 'error'):
        with monkeypatch.context() as interrupted:
            def stop_before_ack(phase, **fields):
                if phase == 'pushed':
                    raise KeyboardInterrupt('fixture interrupted push acknowledgement')
                real_write(phase, **fields)

            interrupted.setattr(pub, 'write_state', stop_before_ack)
            with pytest.raises(KeyboardInterrupt, match='interrupted push'):
                pub.cli()
        if prior_phase == 'error':
            pub.write_state('error', error='legacy interrupted push')
    else:
        faults['deploy' if prior_phase == 'pushed' else 'readback'] = unavailable
        with pytest.raises(RuntimeError, match='fixture stage unavailable'):
            pub.cli()

    before = state.read_bytes()
    previous = json.loads(before)
    sha = git(root, 'rev-parse', 'HEAD')
    assert previous['phase'] == prior_phase and previous['commit'] == sha
    assert git(bare, 'rev-parse', 'HEAD') == sha != baseline
    events.clear()
    at_build = []

    def fail_build(_):
        at_build.append(state.read_bytes())
        unavailable()

    faults['build'] = fail_build
    faults['collect'] = lambda: pytest.fail('recovery fetched bars')
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: pytest.fail('recovery queried Yahoo'))
    with pytest.raises(RuntimeError, match='fixture stage unavailable'):
        pub.cli()
    assert at_build == [state.read_bytes()]
    receipt = json.loads(at_build[0])
    assert receipt['phase'] == ('deployed' if prior_phase == 'deployed' else 'pushed')
    assert receipt['trade_date'] == DAY and receipt['commit'] == sha
    assert receipt['pushed'] is True and receipt['verified'] is False
    assert receipt['deployed'] is (prior_phase == 'deployed')
    if previous['pushed']:
        assert at_build == [before]
    else:
        assert at_build != [before]
        assert receipt['deployment_url'] is None and 'error' not in receipt
    assert [item[0] for item in events] == ['build']
    assert 'fixture stage unavailable' in state.with_suffix('.error.json').read_text()
    assert git(bare, 'rev-parse', 'HEAD') == git(root, 'rev-parse', 'HEAD') == sha
    assert all((root / path).read_text() == value for path, value in drafts.items())


@pytest.mark.parametrize('invalid', ['remote-advanced', 'trade-date', 'index-during-query', 'owned-during-query'])
def test_resume_requires_matching_head_and_valid_candidate_before_push_receipt(close, monkeypatch, tmp_path, invalid):
    root, bare, baseline, state, _, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    real_write = pub.write_state
    real_remote = pub.remote_main

    with monkeypatch.context() as interrupted:
        def stop_before_ack(phase, **fields):
            if phase == 'pushed':
                raise KeyboardInterrupt
            real_write(phase, **fields)

        interrupted.setattr(pub, 'write_state', stop_before_ack)
        with pytest.raises(KeyboardInterrupt):
            pub.cli()

    sha = git(root, 'rev-parse', 'HEAD')
    expected_remote = sha
    assert git(bare, 'rev-parse', 'HEAD') == sha != baseline
    if invalid == 'remote-advanced':
        expected_remote = advance_remote(tmp_path, bare)
    elif invalid == 'trade-date':
        receipt = json.loads(state.read_bytes())
        receipt['trade_date'] = '2026-11-20'
        state.write_text(json.dumps(receipt))
    else:
        def query_then_mutate():
            tip = real_remote()
            if invalid == 'index-during-query':
                git(root, 'add', 'scripts/work.py')
            else:
                (root / pub.US_OWNED_FILES[-1]).write_bytes(b'concurrent owned draft')
            return tip

        monkeypatch.setattr(pub, 'remote_main', query_then_mutate)

    before = state.read_bytes()
    events.clear()
    monkeypatch.setattr(pub, 'latest_spy_date', lambda: pytest.fail('recovery queried Yahoo'))
    faults['collect'] = lambda: pytest.fail('recovery fetched bars')
    with pytest.raises(RuntimeError, match='remote main|ownership or trade date|source index|owned output'):
        pub.cli()
    assert not events and state.read_bytes() == before
    assert git(root, 'rev-parse', 'HEAD') == sha
    assert git(bare, 'rev-parse', 'HEAD') == expected_remote
