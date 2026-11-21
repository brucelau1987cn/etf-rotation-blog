"""Host publisher regressions with network blocked and Git isolated to fixtures."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_low_chip_release_cron import mocked_release as base_mocked_release

LOCAL_POPEN = subprocess.Popen
LOCAL_RUN = subprocess.run

@pytest.fixture(autouse=True)
def offline_transport(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError('publisher regression attempted a real network connection')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def mocked_release(base_mocked_release, monkeypatch):
    mod, state = base_mocked_release
    state['real_pages_verifier'] = state.get('real_pages_verifier', mod.verify_pages_deployment)
    # The shared fixture replaces these gates; retain their module-bound originals.
    import types
    from test_low_chip_release_cron import load_module
    original = load_module()
    history = original.verify_history_index
    state['real_history_verifier'] = types.FunctionType(
        history.__code__, mod.__dict__, history.__name__, history.__defaults__)
    state['real_load_env'] = original.load_env_file
    monkeypatch.setattr(mod, 'verify_pages_deployment',
                        lambda output, candidate: {'pages_url': 'https://candidate.pages.dev'})
    return mod, state

SCRIPT = Path('/root/.hermes/scripts/update_low_chip_and_release.py')
pytestmark = pytest.mark.skipif(not SCRIPT.exists() or os.environ.get('CI') == 'true', reason='host-local publisher')


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('isolated_low_chip', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'ROOT', tmp_path / 'shared')
    mod.ROOT.mkdir()
    for name, rel in {'DATA': mod.DATA_RELATIVE, 'TRACKING': 'public/data/low-chip-tracking.json',
                      'FUYAO_SHADOW': 'public/data/model-lab/low-chip-fuyao-shadow.json',
                      'FTSHARE_INDUSTRY_CACHE': 'public/data/model-lab/ftshare-sw-industry-map.json',
                      'INDEX': 'public/data/low-chip-history-index.json',
                      'HISTORY_DIR': 'public/data/low-chip-history',
                      'RISK_STAGING_SCRIPT': 'scripts/attach_low_chip_risks.py'}.items():
        monkeypatch.setattr(mod, name, mod.ROOT / rel)
    # Fail any missed transport/process mock before it can touch production.
    def forbidden(*args, **kwargs):
        raise AssertionError(f'unmocked external boundary: {args}')
    monkeypatch.setattr(mod.subprocess, 'run', forbidden)
    monkeypatch.setattr(mod.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(mod.urllib.request, 'urlopen', forbidden)
    return mod


def test_main_runs_pipeline_in_detached_worktree(publisher, tmp_path, monkeypatch):
    mod = publisher
    shared = mod.ROOT
    calls = []
    monkeypatch.setattr(mod, 'RELEASE_RUNS', tmp_path / 'runs', raising=False)
    monkeypatch.setattr(mod, 'publish_lock', __import__('contextlib').nullcontext)
    monkeypatch.setattr(mod, 'dirty', lambda: set())
    monkeypatch.setattr(mod, 'staged', lambda: set())
    def run(args, *a, **kw):
        calls.append((mod.ROOT, args))
        return subprocess.CompletedProcess(args, 0, 'base\n' if args[:2] == ['git', 'rev-parse'] else '', '')
    monkeypatch.setattr(mod, 'run', run)
    monkeypatch.setattr(mod, 'RELEASE_STATE', tmp_path / 'state.json')
    def collect():
        assert mod.ROOT != shared, 'long collection still mutates the shared checkout'
        assert mod.DATA == mod.ROOT / mod.DATA_RELATIVE
        assert mod.HISTORY_DIR == mod.ROOT / 'public/data/low-chip-history'
        assert mod.RISK_STAGING_SCRIPT == mod.ROOT / 'scripts/attach_low_chip_risks.py'
        return 1
    monkeypatch.setattr(mod, 'run_pipeline', collect)
    assert mod.main() == 1
    assert mod.ROOT == shared
    assert any(args[:4] == ['git', 'worktree', 'add', '--detach'] for _, args in calls)
    assert not any(args[:2] in (['git', 'reset'], ['git', 'merge']) for _, args in calls)


@pytest.mark.parametrize('hazard', ['head', 'index', 'owned', 'remote'])
def test_shared_drift_blocks_before_d1(mocked_release, monkeypatch, capsys, hazard):
    mod, state = mocked_release
    shared = mod.ROOT / 'shared'
    shared.mkdir()
    monkeypatch.setattr(mod, 'SHARED_ROOT', shared)
    original = mod.run
    original_staged, original_dirty = mod.staged, mod.dirty
    monkeypatch.setattr(mod, 'staged', lambda: {'foreign.py'} if mod.ROOT == shared and hazard == 'index' else original_staged())
    monkeypatch.setattr(mod, 'dirty', lambda: {mod.DATA_RELATIVE} if mod.ROOT == shared and hazard == 'owned' else original_dirty())
    def runner(args, *a, **kw):
        if hazard == 'remote' and args == ['git', 'rev-parse', 'origin/main']:
            return subprocess.CompletedProcess(args, 0, ('weekly-commit' if state['committed'] else 'base') + '\n', '')
        if mod.ROOT == shared and args == ['git', 'rev-parse', 'HEAD']:
            return subprocess.CompletedProcess(args, 0, ('weekly-commit' if hazard == 'head' else 'base') + '\n', '')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod, 'run', runner)
    assert mod.run_pipeline() == 1
    assert state.get('d1_started', False) is False
    assert state['pushed'] is False
    assert 'STAGING BLOCKER:' in capsys.readouterr().out


def test_history_defaults_follow_repository_root_and_runtime_verifier(publisher, tmp_path, monkeypatch):
    mod = publisher
    shared = mod.ROOT
    isolated = tmp_path / 'isolated'
    relative = mod.INDEX.relative_to(shared)
    for root, day in ((shared, '2026-09-17'), (isolated, '2026-09-18')):
        index = root / relative
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({'items': [{'date': day, 'intersection_count': 2}]}))
    reads, calls = [], []
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path == isolated / relative, f'history read escaped isolated checkout: {path}'
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', guarded_read)
    monkeypatch.setattr(mod, 'verify_d1_api', lambda *args: calls.append(args))
    with mod.repository_root(isolated):
        assert mod.verify_history_index('fake-token') == {'dates': 1, 'rows': 2}
    assert reads == [isolated / relative]
    assert calls == [('2026-09-18', 2, 'fake-token')]
    assert mod.ROOT == shared
    assert mod.INDEX == shared / relative


def test_pushed_candidate_fast_forwards_shared_checkout(publisher, tmp_path, monkeypatch):
    mod = publisher
    shared = mod.ROOT
    isolated = tmp_path / 'isolated'
    isolated.mkdir()
    monkeypatch.setattr(mod, 'SHARED_ROOT', shared)
    monkeypatch.setattr(mod, 'dirty', lambda: set())
    monkeypatch.setattr(mod, 'staged', lambda: set())
    calls = []
    def run(args, *a, **kw):
        calls.append((mod.ROOT, args))
        merged = any(c[1][:3] == ['git', 'merge', '--ff-only'] for c in calls)
        return subprocess.CompletedProcess(args, 0, ('candidate' if merged else 'base') + '\n', '')
    monkeypatch.setattr(mod, 'run', run)
    with mod.repository_root(isolated):
        mod.integrate_shared_candidate('base', 'candidate')
    assert (shared, ['git', 'merge', '--ff-only', 'candidate']) in calls
    assert calls[-1] == (shared, ['git', 'rev-parse', 'HEAD'])


@pytest.mark.parametrize('history_fails', [True, False])
@pytest.mark.parametrize('previously_verified', [True, False])
@pytest.mark.parametrize('already_pushed', [True, False])
def test_main_recovery_rechecks_candidate_history_before_push(
        publisher, tmp_path, monkeypatch, capsys, history_fails, previously_verified, already_pushed):
    mod = publisher
    shared = mod.ROOT
    state_file = tmp_path / 'pending.json'
    pending = {'trade_date': '2026-09-18', 'candidate_commit': 'candidate',
               'd1_sync_started': True, 'd1_verified': previously_verified, 'pushed': False}
    state_file.write_text(json.dumps(pending))
    mod.INDEX.parent.mkdir(parents=True)
    mod.INDEX.write_text(json.dumps({'items': [{'date': 'shared-must-stay-unread'}]}))
    monkeypatch.setattr(mod, 'RELEASE_STATE', state_file)
    monkeypatch.setattr(mod, 'RELEASE_RUNS', tmp_path / 'runs')
    monkeypatch.setattr(mod, 'publish_lock', __import__('contextlib').nullcontext)
    monkeypatch.setattr(mod, 'dirty', lambda: set())
    monkeypatch.setattr(mod, 'staged', lambda: set())
    monkeypatch.setattr(mod, 'load_env_file', lambda *a: {'LOW_CHIP_SYNC_TOKEN': 'fake-token'})
    monkeypatch.setattr(mod, 'run_pipeline', lambda: mod.recover_pending_release(state_file))
    def forbidden_deploy(*args):
        raise AssertionError('pending D1 recovery must retain push-only semantics')
    monkeypatch.setattr(mod, 'verify_pages_deployment', forbidden_deploy)
    events = []
    worktrees = []
    pushed = already_pushed

    def runner(args, *a, **kw):
        nonlocal pushed
        if args[:4] == ['git', 'worktree', 'add', '--detach']:
            assert args[-1] == 'candidate'
            root = Path(args[4])
            worktrees.append(root)
            index = root / 'public/data/low-chip-history-index.json'
            index.parent.mkdir(parents=True)
            index.write_text(json.dumps({'items': [
                {'date': '2026-09-18', 'intersection_count': 1},
                {'date': '2026-09-17', 'intersection_count': 2},
            ]}))
            output = ''
        elif args == ['git', 'rev-parse', 'candidate']:
            output = 'candidate'
        elif args == ['git', 'rev-parse', 'origin/main']:
            output = 'candidate' if pushed else 'base'
        elif args == ['git', 'rev-parse', 'candidate^']:
            output = 'base'
        elif args == ['git', 'rev-parse', 'HEAD']:
            output = 'base'
        elif args[:2] == ['git', 'show']:
            output = json.dumps({'data_as_of': '2026-09-18', 'intersection': ['000001.SZ']})
        elif args[:2] == ['git', 'push']:
            events.append('push')
            pushed = True
            output = ''
        else:
            output = ''
        return subprocess.CompletedProcess(args, 0, output, '')

    def verify(day, count, token):
        assert mod.ROOT == worktrees[0]
        assert mod.INDEX == worktrees[0] / 'public/data/low-chip-history-index.json'
        assert token == 'fake-token'
        events.append((day, count))
        if history_fails and day == '2026-09-17':
            raise RuntimeError('historical D1 membership mismatch')
        return {'count': count, 'results': [{'stock_code': '000001'}]}

    monkeypatch.setattr(mod, 'run', runner)
    monkeypatch.setattr(mod, 'verify_d1_api', verify)
    monkeypatch.setattr(mod, 'integrate_shared_candidate', lambda *a: events.append('integrate'))
    assert mod.main() == (1 if history_fails else 0)
    assert events[:3] == [('2026-09-18', 1), ('2026-09-18', 1), ('2026-09-17', 2)]
    saved = json.loads(state_file.read_text())
    if history_fails:
        assert events == [('2026-09-18', 1), ('2026-09-18', 1), ('2026-09-17', 2)]
        assert saved == pending
        assert 'historical D1 membership mismatch' in capsys.readouterr().out
    else:
        assert events[3:] == (['integrate'] if already_pushed else ['push', 'integrate'])
        assert saved['d1_verified'] is True
        assert saved['pushed'] is True
    assert mod.ROOT == shared


def test_recovery_finishes_shared_checkout_integration(publisher, tmp_path, monkeypatch):
    mod = publisher
    state_file = tmp_path / 'pending.json'
    state_file.write_text(json.dumps({'trade_date': '2026-09-18', 'candidate_commit': 'candidate',
                                     'd1_sync_started': True, 'd1_verified': False, 'pushed': False}))
    monkeypatch.setattr(mod, 'load_env_file', lambda *a: {'LOW_CHIP_SYNC_TOKEN': 'fake'})
    monkeypatch.setattr(mod, 'verify_d1_api', lambda *a: {'results': [], 'count': 0})
    mod.INDEX.parent.mkdir(parents=True)
    mod.INDEX.write_text(json.dumps({'items': [{'date': '2026-09-18', 'intersection_count': 0}]}))
    events = []
    def runner(args, *a, **kw):
        if args == ['git', 'rev-parse', 'origin/main']:
            output = 'candidate'
        elif args == ['git', 'rev-parse', 'candidate^']:
            output = 'base'
        elif args[:2] == ['git', 'show']:
            output = json.dumps({'data_as_of': '2026-09-18', 'intersection': []})
        else:
            output = ''
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(mod, 'run', runner)
    monkeypatch.setattr(mod, 'integrate_shared_candidate', lambda *a: events.append(a))
    assert mod.recover_pending_release(state_file) == 0
    assert events == [('base', 'candidate')]
    assert json.loads(state_file.read_text())['pushed'] is True


@pytest.mark.parametrize('case', ['equivalent', 'history-addition', 'history-deletion',
                                 'foreign-history', 'invalid-date', 'gitlink', 'unrelated-parent',
                                 'foreign-candidate', 'history-fails', 'remote-moved',
                                 'shared-head-moved', 'shared-index-changed', 'shared-staged-gitlink',
                                 'shared-owned-dirty'])
def test_reconcile_cherry_picked_release_only_with_matching_git_and_d1(
        publisher, tmp_path, monkeypatch, capsys, case):
    mod = publisher
    root = mod.ROOT
    shared = tmp_path / 'active-main'
    allowed = {'init', 'add', '-c', 'rev-parse', 'switch', 'update-ref', 'merge-base',
               'diff', 'show', 'status', 'diff-index', 'worktree', 'update-index', 'config', 'ls-tree'}
    monkeypatch.setattr(subprocess, 'Popen', LOCAL_POPEN)
    def local_run(args, **kwargs):
        assert args[0] == 'git' and args[1] in allowed and kwargs['cwd'] in {root, shared}
        kwargs['env'] = {key: val for key, val in (kwargs.get('env') or os.environ).items()
                         if not key.startswith('GIT_')}
        kwargs['env'].update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        return LOCAL_RUN(args[:1] + ['-c', 'core.hooksPath=/dev/null'] + args[1:], **kwargs)
    monkeypatch.setattr(subprocess, 'run', local_run)
    def git(*args, cwd=root):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()
    def commit(message):
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', message)
        return git('rev-parse', 'HEAD')

    git('init', '-b', 'main')
    mod.DATA.parent.mkdir(parents=True, exist_ok=True)
    mod.DATA.write_text('{}')
    mod.HISTORY_DIR.mkdir(parents=True)
    dated_file = mod.HISTORY_DIR / '2026-09-17.json'
    research_copy = mod.HISTORY_DIR / '2026-09-17.threshold-2.5.json'
    dated_file.write_text('{}')
    research_copy.write_text('{}')
    first = commit('initial')
    (root / 'parent-marker').write_text('base')
    parent = commit('base')
    mod.DATA.write_text(json.dumps({'data_as_of': '2026-09-18', 'intersection': ['000001.SZ']}))
    mod.INDEX.write_text(json.dumps({'items': [{'date': '2026-09-18', 'intersection_count': 1}]}))
    if case == 'foreign-candidate':
        (root / 'foreign-data').write_text('foreign to low-chip release')
    if case == 'invalid-date':
        (mod.HISTORY_DIR / '2026-02-30.json').write_text('{}')
    if case == 'gitlink':
        git('config', 'diff.ignoreSubmodules', 'all')
        git('add', '.')
        git('update-index', '--add', '--cacheinfo', f'160000,{parent},foreign-submodule')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'candidate')
        candidate = git('rev-parse', 'HEAD')
        assert git('ls-tree', candidate, 'foreign-submodule').startswith('160000 commit ')
    else:
        candidate = commit('candidate')
    git('switch', '-c', 'upstream', first if case == 'unrelated-parent' else parent)
    (root / 'upstream-marker').write_text('concurrent publish')
    commit('upstream work')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'cherry-pick', candidate)
    if case == 'history-addition':
        (mod.HISTORY_DIR / '2026-09-16.json').write_text('{}')
        commit('additional history on remote')
    if case == 'history-deletion':
        dated_file.unlink()
        commit('deleted history on remote')
    if case == 'foreign-history':
        research_copy.write_text('{"unowned":true}')
        commit('changed unrelated history experiment')
    origin = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/main', origin)
    git('switch', '--detach', candidate)
    git('worktree', 'add', '--detach', str(shared), origin)
    monkeypatch.setattr(mod, 'SHARED_ROOT', shared)

    state = tmp_path / 'pending.json'
    original = {'trade_date': '2026-09-18', 'candidate_commit': candidate,
                'd1_sync_started': True, 'd1_verified': False, 'pushed': False}
    original_bytes = json.dumps(original).encode()
    state.write_bytes(original_bytes)
    real_run = mod.run
    def run(args, *a, **kw):
        op = args[1]
        assert op not in ('push', 'merge')
        if op in ('fetch', 'ls-remote'):
            sha = parent if case == 'remote-moved' else origin
            return subprocess.CompletedProcess(args, 0, f'{sha}\trefs/heads/main\n', '')
        return real_run(args, *a, **kw)
    events = []
    real_gate = mod.shared_checkout_gate
    def gate(commit):
        events.append(commit)
        real_gate(commit)
    def verify_history(_token):
        events.append('history')
        if case == 'history-fails':
            raise RuntimeError('history mismatch')
        if case == 'shared-head-moved':
            git('update-ref', 'HEAD', parent, cwd=shared)
        if case == 'shared-index-changed':
            (shared / 'foreign-staged').write_text('in progress')
            git('add', 'foreign-staged', cwd=shared)
        if case == 'shared-staged-gitlink':
            git('config', 'diff.ignoreSubmodules', 'all', cwd=shared)
            git('update-index', '--add', '--cacheinfo', f'160000,{parent},foreign-submodule', cwd=shared)
        if case == 'shared-owned-dirty':
            (shared / mod.DATA_RELATIVE).write_text('{"concurrent":true}')
    monkeypatch.setattr(mod, 'run', run)
    monkeypatch.setattr(mod, 'shared_checkout_gate', gate)
    monkeypatch.setattr(mod, 'load_env_file', lambda *a: {'LOW_CHIP_SYNC_TOKEN': 'mock'})
    monkeypatch.setattr(mod, 'verify_d1_api', lambda *a: {'results': [{'stock_code': '000001'}]})
    monkeypatch.setattr(mod, 'verify_history_index', verify_history)

    accepted = case in {'equivalent', 'foreign-history'}
    assert mod.recover_pending_release(state) == (0 if accepted else 1)
    if accepted:
        assert events == [origin, 'history', origin]
        assert json.loads(state.read_text()) == dict(original, candidate_commit=origin,
                                                    d1_verified=True, pushed=True)
    else:
        expected_events = []
        if case.startswith('shared-'):
            expected_events = [origin, 'history', origin]
        elif case in {'history-fails', 'remote-moved'}:
            expected_events = [origin, 'history']
        assert events == expected_events
        assert state.read_bytes() == original_bytes
        if case == 'shared-staged-gitlink':
            assert any(row.get('status') == 'STAGING BLOCKER' and 'shared index changed' in row.get('reason', '')
                       for row in map(json.loads, capsys.readouterr().out.splitlines()))


@pytest.mark.parametrize('api_case', [
    'success', 'preview', 'failed-stage', 'wrong-commit', 'wrong-url', 'wrong-id',
    'missing-deployment', 'list-only-success', 'api-failure', 'missing-token',
    'ambiguous-account', 'account-discovery', 'missing-url',
])
def test_detached_release_pins_pages_production_branch_and_candidate(
        mocked_release, monkeypatch, capsys, api_case):
    import sys
    import types
    from io import BytesIO

    mod, state = mocked_release
    runner = mod.run
    commands = []
    probe_urls = []
    fake_pages = types.ModuleType('scripts.pages_release')

    def release_pages(urls):
        probe_urls.extend(urls)
        subprocess.run(['npm', 'run', 'build'])
        subprocess.run(['wrangler', 'pages', 'deploy', 'dist', '--project-name', 'etf'])
        return 'https://candidate.pages.dev'

    setattr(fake_pages, 'release_pages', release_pages)
    account_url = 'https://api.cloudflare.com/client/v4/accounts?per_page=50'
    base = 'https://api.cloudflare.com/client/v4/accounts/account/pages/projects/etf-rotation-blog'
    list_url = base + '/deployments?per_page=25'
    exact_url = base + '/deployments/deployment'
    preview = 'https://candidate.pages.dev'
    good = {'id': 'deployment', 'url': preview, 'environment': 'production',
            'latest_stage': {'status': 'success'},
            'deployment_trigger': {'metadata': {'commit_hash': 'candidate'}}}
    exact = json.loads(json.dumps(good))
    if api_case == 'preview':
        exact['environment'] = 'preview'
    elif api_case in {'failed-stage', 'list-only-success'}:
        exact['latest_stage']['status'] = 'failure'
    elif api_case == 'wrong-commit':
        exact['deployment_trigger']['metadata']['commit_hash'] = 'other'
    elif api_case == 'wrong-url':
        exact['url'] = 'https://other.pages.dev'
    elif api_case == 'wrong-id':
        exact['id'] = 'other'
    requests = []

    def opener(request, timeout=30):
        assert request.get_header('Authorization') == 'Bearer fake-pages-token'
        assert timeout == 30
        requests.append(request.full_url)
        if request.full_url == account_url:
            result = [{'id': 'account'}]
            if api_case == 'ambiguous-account':
                result.append({'id': 'second'})
        elif request.full_url == list_url:
            # A same-commit deployment at a different URL cannot identify this upload.
            result = [{'id': 'other', 'url': 'https://other.pages.dev',
                       **{k: v for k, v in good.items() if k not in {'id', 'url'}}}]
            if api_case != 'missing-deployment':
                result.append(good)
        else:
            assert request.full_url == exact_url
            result = exact
        return BytesIO(json.dumps({'success': api_case != 'api-failure', 'result': result}).encode())

    pages_env = mod.ROOT / 'pages.env'
    values = {'CLOUDFLARE_API_TOKEN': 'fake-pages-token', 'CLOUDFLARE_ACCOUNT_ID': 'account'}
    if api_case == 'missing-token':
        values.pop('CLOUDFLARE_API_TOKEN')
    if api_case in {'ambiguous-account', 'account-discovery'}:
        values.pop('CLOUDFLARE_ACCOUNT_ID')
    pages_env.write_text('\n'.join(f'{key}={value}' for key, value in values.items()))
    real_load_env = state['real_load_env']
    monkeypatch.setattr(mod, 'load_env_file',
                        lambda path, required=(): real_load_env(path, required) if path == pages_env else {})
    monkeypatch.setattr(mod, 'PAGES_ENV', pages_env)
    monkeypatch.setattr(mod, 'verify_pages_deployment', state['real_pages_verifier'])
    monkeypatch.setattr(mod.urllib.request, 'urlopen', opener)

    def command(args, *a, **kw):
        if len(args) > 2 and args[1] == '-c' and 'release_pages' in args[2]:
            with monkeypatch.context() as child:
                child.setitem(sys.modules, 'scripts.pages_release', fake_pages)
                child.setattr(subprocess, 'run', lambda args, *a, **kw: commands.append(args))
                exec(compile(args[2], '<release-child>', 'exec'), {})
            output = '' if api_case == 'missing-url' else 'Deployment complete! https://candidate.pages.dev'
            return subprocess.CompletedProcess(args, 0, output, '')
        return runner(args, *a, **kw)

    monkeypatch.setattr(mod, 'run', command)
    success = api_case in {'success', 'account-discovery'}
    assert mod.run_pipeline() == (0 if success else 1)
    if success:
        assert requests[-2:] == [list_url, exact_url]
    elif api_case in {'preview', 'failed-stage', 'wrong-commit', 'wrong-url', 'wrong-id', 'list-only-success'}:
        assert requests[-1] == exact_url
    output = capsys.readouterr().out
    assert ('🟢 低筹码股' in output) is success
    assert commands == [
        ['npm', 'run', 'build'],
        ['wrangler', 'pages', 'deploy', 'dist', '--project-name', 'etf',
         '--branch', 'main', '--commit-hash', 'candidate'],
    ]
    assert probe_urls == ['https://etf.peekabo.cc/login/']
    assert state['pushed'] is True


def test_main_history_failure_blocks_push_and_deploy(mocked_release, monkeypatch, capsys):
    mod, state = mocked_release
    mod.INDEX.write_text(json.dumps({'items': [
        {'date': '2026-09-18', 'intersection_count': 1},
        {'date': '2026-09-17', 'intersection_count': 0},
    ]}))
    monkeypatch.setattr(mod, 'verify_history_index', state['real_history_verifier'])
    calls = []

    def verify(day, count, token):
        calls.append((day, count, token))
        if day == '2026-09-17':
            raise RuntimeError('historical membership gate failed')
        return {'count': count}

    monkeypatch.setattr(mod, 'verify_d1_api', verify)
    assert mod.run_pipeline() == 1
    assert calls == [('2026-09-18', 1, 'token'), ('2026-09-18', 1, 'token'),
                     ('2026-09-17', 0, 'token')]
    assert state['d1_started'] is True
    assert state['pushed'] is False
    assert not any(len(args) > 2 and args[1] == '-c' and 'release_pages' in args[2]
                   for args in state['calls'])
    assert 'historical membership gate failed' in capsys.readouterr().out


@pytest.mark.parametrize('boundary', ['success', 'build', 'd1'])
def test_main_complete_isolated_orchestration(mocked_release, monkeypatch, tmp_path, boundary):
    mod, state = mocked_release
    shared = mod.ROOT
    monkeypatch.setattr(mod, 'RELEASE_RUNS', tmp_path / 'runs')
    monkeypatch.setattr(mod, 'RELEASE_STATE', tmp_path / 'absent-state')
    monkeypatch.setattr(mod, 'publish_lock', __import__('contextlib').nullcontext)
    monkeypatch.setattr(mod, 'RISK_STAGING_SCRIPT', shared / 'scripts/attach_low_chip_risks.py')
    originals = {rel: (shared / rel).read_bytes() for rel in mod.release_paths() if (shared / rel).exists()}
    runner = mod.run
    shared_head = 'base'
    def command(args, *a, **kw):
        nonlocal shared_head
        if args[:4] == ['git', 'worktree', 'add', '--detach']:
            target = Path(args[4])
            for rel, body in originals.items():
                path = target / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
        if mod.ROOT == shared and args[:2] == ['git', 'rev-parse']:
            return subprocess.CompletedProcess(args, 0, shared_head + '\n', '')
        if mod.ROOT == shared and args[:3] == ['git', 'merge', '--ff-only']:
            shared_head = 'candidate'
        if args == ['npm', 'run', 'build']:
            if boundary == 'build':
                raise RuntimeError('mock build failed')
            for rel, body in originals.items():
                target = mod.ROOT / 'dist' / rel.removeprefix('public/')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((mod.ROOT / rel).read_bytes())
            state['calls'].append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        return runner(args, *a, **kw)
    monkeypatch.setattr(mod, 'run', command)
    if boundary == 'd1':
        monkeypatch.setattr(mod, 'sync_d1_metrics', lambda *a: (_ for _ in ()).throw(RuntimeError('mock D1 failed')))
    rollbacks = []
    monkeypatch.setattr(mod, 'rollback_candidate', lambda *a: rollbacks.append(a))
    assert mod.main() == (0 if boundary == 'success' else 1)
    assert mod.ROOT == shared
    assert not rollbacks
    assert shared_head == ('candidate' if boundary == 'success' else 'base')
    assert {rel: (shared / rel).read_bytes() for rel in originals} == originals
    assert list((tmp_path / 'runs').glob('run-*')), 'isolated output must remain recoverable'
