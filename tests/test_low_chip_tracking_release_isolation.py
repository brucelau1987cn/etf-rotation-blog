"""Offline regression contract for the independently scheduled tracking publisher."""
import contextlib
import importlib.util
import json
import socket
import subprocess
from pathlib import Path

import pytest

PUBLISHER = Path('/root/.hermes/scripts/update_low_chip_tracking_and_release.py')
DAY = '2026-11-17'
TRACKING = 'public/data/low-chip-tracking.json'
CURRENT = 'public/data/a-low-chip-stocks.json'
MAP = 'public/data/model-lab/ftshare-sw-industry-map.json'


@pytest.fixture
def mod(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('tracking_release_under_test', PUBLISHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def blocked(*args, **kwargs):
        raise AssertionError('real transport/process forbidden in offline test')
    monkeypatch.setattr(subprocess, 'run', blocked)
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'shared')
    monkeypatch.setattr(module, 'TRACKING', module.ROOT / TRACKING)
    return module


def record(day=DAY, **extra):
    return {'first_seen': '2026-11-16', 'industry_source': 'fixture',
            'industry_standard': 'SW2021', 'industry_level2': {},
            'theme_concepts': [], 'industry_display': 'fixture', 'daily': [
        {'date': day, 'close': 10, 'profit_ratio': 12}], **extra}


def payload(stocks=None):
    return {'schema_version': 'low-chip-tracking-v1', 'generated_at': DAY,
            'data_through': DAY, 'stocks': stocks or {'600001.SH': record()}}


def test_coverage_checks_each_unfinished_stock(mod):
    data = payload({'600001.SH': record(), '600002.SH': record('2026-11-16')})
    with pytest.raises(RuntimeError, match='600002.SH'):
        mod.validate_tracking_coverage(data, DAY, {'600001.SH', '600002.SH'})


def test_coverage_rejects_dropped_or_empty_pool(mod):
    with pytest.raises(RuntimeError, match='missing|empty'):
        mod.validate_tracking_coverage({'schema_version': 'low-chip-tracking-v1', 'stocks': {}}, DAY, {'600001.SH'})


def test_coverage_ignores_timestamps_and_completion_flag(mod):
    with pytest.raises(RuntimeError, match='600001.SH'):
        mod.validate_tracking_coverage(payload({'600001.SH': record('2026-11-16', tracking_complete=True)}), DAY, {'600001.SH'})


def test_coverage_allows_completed_window_and_dated_halt(mod):
    import datetime
    completed = record(first_seen='2026-10-01')
    completed['daily'] = [{'date': (datetime.date(2026, 10, 1) + datetime.timedelta(days=i)).isoformat(), 'close': 10} for i in range(21)]
    data = payload({'600001.SH': completed, '600002.SH': record('2026-11-16', coverage={'status': 'halted', 'target_day': DAY, 'reason': 'exchange suspension'})})
    result = mod.validate_tracking_coverage(data, DAY, set(data['stocks']))
    assert result == {'stocks': 2, 'complete': 1, 'covered': 0, 'halted': 1, 'target_day': DAY}


@pytest.mark.parametrize('coverage', [
    {'status': 'halted', 'target_day': '2026-11-16', 'reason': 'suspension'},
    {'status': 'halted', 'target_day': DAY, 'reason': ''},
    {'status': 'unavailable', 'target_day': DAY, 'reason': 'no data'},
])
def test_coverage_unproven_halt_or_failure_blocks(mod, coverage):
    with pytest.raises(RuntimeError, match='600001.SH'):
        mod.validate_tracking_coverage(payload({'600001.SH': record('2026-11-16', coverage=coverage)}), DAY, {'600001.SH'})


@pytest.fixture
def pipeline(mod, monkeypatch, tmp_path):
    shared = mod.ROOT
    shared.mkdir()
    isolated = tmp_path / 'isolated'
    isolated.mkdir()
    calls = []
    state = {'head': 'base', 'shared_head': 'base', 'dirty': set(), 'staged': set(),
             'remote': 'base', 'fail': None}
    monkeypatch.setattr(mod, 'RELEASE_RUNS', tmp_path)
    real_integrate = mod.integrate_shared
    def put(root, rel, value):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for root in (shared, isolated):
        put(root, TRACKING, payload({'600001.SH': record('2026-11-16')}))
        put(root, CURRENT, {'intersection': ['600001.SH']})
        put(root, MAP, {})
        put(root, 'public/data/low-chip-history/2026-11-16.json', {'intersection': ['600001.SH']})
    def fake_run(args, timeout=900, **kwargs):
        calls.append((mod.ROOT, args))
        text = ''
        if state['fail'] and state['fail'] in ' '.join(args):
            raise RuntimeError('injected stage failure')
        if args[0] == 'git':
            op = args[1]
            if op == 'rev-parse':
                text = (state['head'] if mod.ROOT == isolated else state['shared_head']) if args[2] == 'HEAD' else state['remote']
            elif op == 'worktree' and args[2] == 'list':
                text = f'worktree {isolated}\x00HEAD candidate\x00detached\x00\x00'
            elif op == 'merge':
                state['shared_head'] = args[-1]
            elif op == 'status':
                text = ''.join(' M ' + p + '\0' for p in state['dirty']) if mod.ROOT == isolated else ''
            elif op == 'diff' and '--cached' in args:
                text = ''.join(p + '\0' for p in state['staged']) if mod.ROOT == isolated else ''
            elif op == 'add':
                state['approved'] = set(args[3:])
                state['staged'] = set(args[3:])
            elif op == 'commit':
                state['head'] = 'candidate'; state['dirty'] = set(); state['staged'] = set()
                state['blobs'] = {p: (isolated / p).read_bytes() for p in mod.OWNED}
            elif op == 'show' and '--format=%P' in args:
                text = 'base'
            elif op == 'show':
                revision, rel = args[-1].split(':', 1)
                if revision == 'base':
                    text = (shared / rel).read_bytes()
                elif revision == 'candidate':
                    text = state['blobs'][rel]
                else:
                    text = (isolated / rel).read_bytes()
            elif op == 'diff-tree':
                text = ''.join(p + '\0' for p in sorted(state.get('approved', {TRACKING})))
            elif op == 'ls-remote':
                text = state['remote'] + '\trefs/heads/main'
            elif op == 'push':
                state['remote'] = 'candidate'
        elif 'scripts/update_low_chip_tracking.py' in args:
            put(isolated, TRACKING, payload()); state['dirty'] = {TRACKING}
        elif 'scripts/build_site.py' in args:
            assert mod.ROOT == isolated
            import shutil
            shutil.copytree(isolated / 'public', isolated / 'dist', dirs_exist_ok=True)
        return subprocess.CompletedProcess(args, 0, text, '')
    monkeypatch.setattr(mod, 'run', fake_run)
    monkeypatch.setattr(mod, 'create_worktree', lambda base: isolated)
    monkeypatch.setattr(mod, 'calendar_target', lambda: DAY)
    monkeypatch.setattr(mod, 'publish_lock', contextlib.nullcontext)
    state['real_deploy'] = mod.deploy_and_verify
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda candidate, digest: {'candidate_commit': candidate, 'pages_api': 'success'})
    def integrate(*args):
        real_integrate(*args)
        calls.append((shared, ['INTEGRATE']))
    monkeypatch.setattr(mod, 'integrate_shared', integrate)
    return mod, calls, state, isolated


def test_pipeline_collects_validates_builds_in_detached_checkout(pipeline):
    mod, calls, state, isolated = pipeline
    assert mod.main() == 0
    commands = [args for root, args in calls]
    assert any('scripts/update_low_chip_tracking.py' in a for a in commands)
    assert any('-m' in a and 'pytest' in a for a in commands)
    assert any('scripts/build_site.py' in a and '--no-contract-chain' in a for a in commands)
    assert all(root == isolated for root, a in calls if a[0] != 'git' and a[0] != 'INTEGRATE')
    assert commands.index(['INTEGRATE']) > next(i for i, a in enumerate(commands) if a[:2] == ['git', 'push'])
    assert mod.ROOT != isolated


@pytest.mark.parametrize('stage', ['scripts/update_low_chip_tracking.py', 'pytest', 'scripts/build_site.py', 'commit', 'push'])
def test_pipeline_failure_retains_candidate_without_shared_checkout(pipeline, capsys, stage):
    mod, calls, state, isolated = pipeline
    state['fail'] = stage
    assert mod.main() != 0
    assert isolated.exists()
    assert 'STAGING BLOCKER' in capsys.readouterr().out
    assert not any(a[0] == 'INTEGRATE' for _, a in calls)
    assert not any(a[:2] in (['git', 'checkout'], ['git', 'reset']) for _, a in calls)


def test_shared_owned_support_dirt_blocks_before_collection(pipeline, monkeypatch):
    mod, calls, _, isolated = pipeline
    original = mod.dirty_paths
    monkeypatch.setattr(mod, 'dirty_paths', lambda: {CURRENT} if mod.ROOT != isolated else original())
    assert mod.main() != 0
    assert not any('scripts/update_low_chip_tracking.py' in a for _, a in calls)


def test_shared_foreign_dirt_stays_outside_isolated_build(pipeline, monkeypatch):
    mod, calls, _, isolated = pipeline
    foreign = mod.ROOT / 'src/foreign.txt'
    foreign.parent.mkdir(); foreign.write_bytes(b'foreign producer')
    original = mod.dirty_paths
    monkeypatch.setattr(mod, 'dirty_paths', lambda: {'src/foreign.txt'} if mod.ROOT != isolated else original())
    assert mod.main() == 0
    assert foreign.read_bytes() == b'foreign producer'
    assert not (isolated / 'src/foreign.txt').exists()


@pytest.mark.parametrize('kind', ['parent', 'paths', 'source-bytes', 'dist-bytes', 'shared-head', 'origin'])
def test_candidate_tampering_blocks_push(pipeline, monkeypatch, kind):
    mod, calls, state, isolated = pipeline
    original = mod.run
    def tamper(args, *a, **kw):
        result = original(args, *a, **kw)
        if kind == 'parent' and args[:3] == ['git', 'show', '-s']:
            result.stdout = 'foreign-parent'
        elif kind == 'paths' and args[:2] == ['git', 'diff-tree']:
            result.stdout = TRACKING + '\0foreign.txt\0'
        elif kind == 'source-bytes' and args[:2] == ['git', 'commit']:
            (isolated / TRACKING).write_text('{}')
        elif kind == 'dist-bytes' and args[:2] == ['git', 'commit']:
            (isolated / 'dist/data/low-chip-tracking.json').write_text('{}')
        elif kind == 'shared-head' and args[:3] == ['git', 'rev-parse', 'HEAD'] and mod.ROOT != isolated and state['head'] == 'candidate':
            result.stdout = 'foreign-head'
        elif kind == 'origin' and args[:3] == ['git', 'rev-parse', 'origin/main'] and state['head'] == 'candidate':
            result.stdout = 'foreign-origin'
        return result
    monkeypatch.setattr(mod, 'run', tamper)
    assert mod.main() != 0
    assert not any(args[:2] == ['git', 'push'] for _, args in calls)


def test_postpush_deploy_failure_retains_candidate_without_shared_integration(pipeline, monkeypatch, capsys):
    mod, calls, _, _ = pipeline
    def failed(*args):
        raise RuntimeError('Pages verification unavailable')
    monkeypatch.setattr(mod, 'deploy_and_verify', failed)
    assert mod.main() != 0
    assert any(args[:2] == ['git', 'push'] for _, args in calls)
    assert not any(args == ['INTEGRATE'] for _, args in calls)
    assert '"pushed": true' in capsys.readouterr().out


def test_postpush_failure_resumes_verified_candidate_then_ff(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    accepted = mod.deploy_and_verify
    attempts = []
    def deploy(candidate, digest):
        attempts.append(candidate)
        if len(attempts) == 1:
            raise RuntimeError('Pages unavailable after push')
        assert state['shared_head'] == 'base'
        return accepted(candidate, digest)
    monkeypatch.setattr(mod, 'deploy_and_verify', deploy)
    assert mod.main() == 1
    assert state['remote'] == 'candidate' and state['shared_head'] == 'base'
    calls.clear()
    assert mod.main(['--resume-worktree', str(isolated)]) == 0
    commands = [args for _, args in calls]
    assert attempts == ['candidate', 'candidate']
    assert state['shared_head'] == state['remote'] == 'candidate'
    assert any('pytest' in a for a in commands)
    assert any('scripts/build_site.py' in a for a in commands)
    assert not any('scripts/update_low_chip_tracking.py' in a or
                   'scripts/refresh_low_chip_ftshare_industry.py' in a for a in commands)
    assert not any(a[:2] in (['git', 'push'], ['git', 'commit']) for a in commands)
    assert commands[-1] == ['INTEGRATE']


@pytest.mark.parametrize('mutation', [
    'unknown', 'remote', 'shared-head', 'shared-owned', 'shared-staged',
    'candidate-dirty', 'candidate-staged', 'parent', 'paths', 'source',
    'fixture-failure', 'build-source', 'build-dist', 'remote-after-deploy',
])
def test_resume_fails_closed_before_shared_ff(pipeline, monkeypatch, mutation):
    mod, calls, state, isolated = pipeline
    def failed(*args):
        raise RuntimeError('first deploy failed')
    monkeypatch.setattr(mod, 'deploy_and_verify', failed)
    assert mod.main() == 1
    calls.clear()
    attempts = []
    def accepted(*args):
        attempts.append(args)
        if mutation == 'remote-after-deploy':
            state['remote'] = 'moved'
        return {'candidate_commit': 'candidate', 'pages_api': 'success'}
    monkeypatch.setattr(mod, 'deploy_and_verify', accepted)
    path = isolated
    if mutation == 'unknown':
        path = isolated.parent / 'unknown'
        path.mkdir()
    elif mutation == 'remote':
        state['remote'] = 'moved'
    elif mutation == 'shared-head':
        state['shared_head'] = 'moved'
    elif mutation == 'candidate-dirty':
        state['dirty'] = {TRACKING}
    elif mutation == 'candidate-staged':
        state['staged'] = {TRACKING}
    elif mutation == 'source':
        (isolated / TRACKING).write_text('{}')
    elif mutation in {'shared-owned', 'shared-staged'}:
        original = mod.dirty_paths if mutation == 'shared-owned' else mod.staged_paths
        def dirt():
            return {CURRENT} if mod.ROOT != isolated else original()
        monkeypatch.setattr(mod, 'dirty_paths' if mutation == 'shared-owned' else 'staged_paths', dirt)
    original_run = mod.run
    def tamper(args, *a, **kw):
        result = original_run(args, *a, **kw)
        if mutation == 'parent' and args[:3] == ['git', 'show', '-s']:
            result.stdout = 'unknown-parent'
        elif mutation == 'paths' and args[:2] == ['git', 'diff-tree']:
            result.stdout = TRACKING + '\0foreign.txt\0'
        elif mutation == 'fixture-failure' and 'pytest' in args:
            raise RuntimeError('candidate fixture validation failed')
        elif 'scripts/build_site.py' in args:
            if mutation == 'build-source':
                (isolated / TRACKING).write_text('{}')
            elif mutation == 'build-dist':
                (isolated / 'dist/data/low-chip-tracking.json').write_text('{}')
        return result
    monkeypatch.setattr(mod, 'run', tamper)
    assert mod.main(['--resume-worktree', str(path)]) == 1
    assert not any(a[:2] in (['git', 'push'], ['git', 'commit'], ['git', 'merge'])
                   for _, a in calls)
    assert not any('scripts/update_low_chip_tracking.py' in a for _, a in calls)
    assert bool(attempts) == (mutation == 'remote-after-deploy')


def test_resume_revalidates_build_instead_of_trusting_local_manifest(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    def failed(*args):
        raise RuntimeError('deploy failure')
    monkeypatch.setattr(mod, 'deploy_and_verify', failed)
    assert mod.main() == 1
    calls.clear()
    # A made-up local receipt cannot authorize an invalid retained source blob.
    (isolated / 'receipt.json').write_text(json.dumps({'status': 'published', 'validated': True}))
    original = mod.run
    def broken_fixture(args, *a, **kw):
        if 'pytest' in args:
            raise RuntimeError('real validation required')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod, 'run', broken_fixture)
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda *a: pytest.fail('unverified deploy'))
    assert mod.main(['--resume-worktree', str(isolated)]) == 1
    assert not any(a[:2] == ['git', 'merge'] for _, a in calls)


@pytest.mark.parametrize('verdict', ['success', 'wrong-commit', 'preview', 'failed'])
def test_resume_uses_exact_pages_api_acceptance_before_ff(pipeline, monkeypatch, tmp_path, verdict):
    import io
    mod, calls, state, isolated = pipeline
    def failed(*args):
        raise RuntimeError('initial Pages failed')
    monkeypatch.setattr(mod, 'deploy_and_verify', failed)
    assert mod.main() == 1
    calls.clear()
    creds = tmp_path / 'pages.env'
    creds.write_text('CLOUDFLARE_API_TOKEN=fixture\nCLOUDFLARE_ACCOUNT_ID=account\n')
    monkeypatch.setattr(mod, 'PAGES_ENV', creds)
    preview = 'https://resume.etf-rotation-blog.pages.dev'
    dep = {'id': 'resumed-id', 'url': preview,
           'environment': 'preview' if verdict == 'preview' else 'production',
           'latest_stage': {'status': 'failure' if verdict == 'failed' else 'success'},
           'deployment_trigger': {'metadata': {
               'commit_hash': 'foreign' if verdict == 'wrong-commit' else 'candidate'}}}
    requests = []
    def opener(request, **kw):
        requests.append(request.full_url)
        result = [dep] if request.full_url.endswith('per_page=25') else dep
        return io.BytesIO(json.dumps({'success': True, 'result': result}).encode())
    monkeypatch.setattr(mod.urllib.request, 'urlopen', opener)
    original = mod.run
    def child(args, *a, **kw):
        if '-c' in args and 'out = deploy_pages()' in args[-1]:
            calls.append((mod.ROOT, args))
            return subprocess.CompletedProcess(args, 0, 'Deployment complete ' + preview, '')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod, 'run', child)
    monkeypatch.setattr(mod, 'deploy_and_verify', state['real_deploy'])
    assert mod.main(['--resume-worktree', str(isolated)]) == (0 if verdict == 'success' else 1)
    assert requests[-1].endswith('/deployments/resumed-id')
    assert state['shared_head'] == ('candidate' if verdict == 'success' else 'base')
    assert not any(a[:2] == ['git', 'push'] for _, a in calls)


def test_isolated_support_changes_require_this_runs_enrichment(pipeline, monkeypatch):
    mod, calls, _, isolated = pipeline
    original = mod.run
    def dirty_support(args, *a, **kw):
        result = original(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            (isolated / CURRENT).write_text('{"foreign":"unsupported"}')
        if args[:2] == ['git', 'status'] and mod.ROOT == isolated:
            result.stdout += ' M ' + CURRENT + '\0'
        return result
    monkeypatch.setattr(mod, 'run', dirty_support)
    assert mod.main() != 0
    assert not any(args[:2] == ['git', 'commit'] for _, args in calls)


@pytest.fixture
def pages(mod, monkeypatch, tmp_path):
    import io
    creds = tmp_path / 'pages.env'
    creds.write_text('CLOUDFLARE_API_TOKEN=fixture\nCLOUDFLARE_ACCOUNT_ID=account\n')
    monkeypatch.setattr(mod, 'PAGES_ENV', creds)
    (mod.ROOT / 'dist').mkdir(parents=True)
    (mod.ROOT / 'dist/file').write_text('built')
    preview = 'https://abc123.etf-rotation-blog.pages.dev'
    dep = {'id': 'deployment-id', 'url': preview, 'environment': 'production',
           'latest_stage': {'status': 'success'},
           'deployment_trigger': {'metadata': {'commit_hash': 'candidate'}}}
    requests = []
    def opener(request, **kw):
        requests.append(request.full_url)
        result = [dep] if request.full_url.endswith('per_page=25') else dep
        return io.BytesIO(json.dumps({'success': True, 'result': result}).encode())
    monkeypatch.setattr(mod.urllib.request, 'urlopen', opener)
    commands = []
    def child(args, *a, **kw):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, 'Deployment complete ' + preview, '')
    monkeypatch.setattr(mod, 'run', child)
    return mod, dep, requests, commands


def test_pages_requires_exact_commit_environment_and_success(pages):
    mod, dep, requests, commands = pages
    result = mod.deploy_and_verify('candidate', mod.tree_digest(mod.ROOT / 'dist'))
    assert result['deployment_id'] == 'deployment-id'
    assert requests[-1].endswith('/deployments/deployment-id')
    assert '--branch' in commands[0][-1] and '--commit-hash' in commands[0][-1]


@pytest.mark.parametrize('mutation', ['commit', 'preview', 'stage', 'missing-token', 'api-unavailable'])
def test_pages_failures_are_mandatory_blockers(pages, monkeypatch, mutation):
    mod, dep, requests, commands = pages
    if mutation == 'commit':
        dep['deployment_trigger']['metadata']['commit_hash'] = 'foreign'
    elif mutation == 'preview':
        dep['environment'] = 'preview'
    elif mutation == 'stage':
        dep['latest_stage']['status'] = 'failure'
    elif mutation == 'missing-token':
        mod.PAGES_ENV.write_text('CLOUDFLARE_ACCOUNT_ID=account\n')
    else:
        def unavailable(*args, **kw):
            raise OSError('offline')
        monkeypatch.setattr(mod.urllib.request, 'urlopen', unavailable)
    with pytest.raises((RuntimeError, OSError)):
        mod.deploy_and_verify('candidate', mod.tree_digest(mod.ROOT / 'dist'))


def test_producer_support_mutation_is_blocked_even_when_enrichment_needed(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    original = mod.run
    def mutate(args, *a, **kw):
        result = original(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            data = json.loads((isolated / TRACKING).read_text())
            del data['stocks']['600001.SH']['industry_source']
            (isolated / TRACKING).write_text(json.dumps(data))
            (isolated / CURRENT).write_text('{"unexpected":true}')
            state['dirty'].add(CURRENT)
        return result
    monkeypatch.setattr(mod, 'run', mutate)
    assert mod.main() != 0
    assert not any('scripts/refresh_low_chip_ftshare_industry.py' in args for _, args in calls)


def test_validation_cannot_create_dirt_on_unchanged_skip(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    original = mod.run
    def mutate(args, *a, **kw):
        result = original(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            state['dirty'] = set()
        if 'pytest' in args:
            state['dirty'].add('foreign.txt')
        return result
    monkeypatch.setattr(mod, 'run', mutate)
    assert mod.main() != 0


def test_revision_hashes_verify_actual_git_blob(mod, monkeypatch):
    import hashlib
    expected = {TRACKING: hashlib.sha256(b'validated').hexdigest()}
    monkeypatch.setattr(mod, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, b'changed', b''))
    with pytest.raises(RuntimeError, match='validated bytes differ'):
        mod.verify_revision_hashes(expected, 'candidate')


def test_run_uses_candidate_cwd_and_sanitizes_python_env(mod, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/foreign/python')
    monkeypatch.setenv('PYTHONHOME', '/foreign/home')
    seen = []
    def child(args, **kw):
        seen.append(kw)
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', child)
    mod.run(['python', 'script'])
    assert seen[0]['cwd'] == mod.ROOT
    assert 'PYTHONPATH' not in seen[0]['env'] and 'PYTHONHOME' not in seen[0]['env']


@pytest.mark.parametrize('gate', ['staged', 'head-origin'])
def test_shared_initial_gates_run_before_collection(pipeline, monkeypatch, gate):
    mod, calls, state, isolated = pipeline
    if gate == 'staged':
        monkeypatch.setattr(mod, 'staged_paths', lambda: {'foreign.txt'})
    else:
        state['remote'] = 'foreign'
    assert mod.main() != 0
    assert not any('scripts/update_low_chip_tracking.py' in args for _, args in calls)


def test_actual_worktree_setup_is_detached_and_links_only_dependencies(mod, monkeypatch, tmp_path):
    mod.ROOT.mkdir()
    (mod.ROOT / 'node_modules').mkdir()
    (mod.ROOT / '.build-venv').mkdir()
    (mod.ROOT / 'foreign.json').write_text('shared foreign edit')
    monkeypatch.setattr(mod, 'RELEASE_RUNS', tmp_path / 'runs')
    calls = []
    monkeypatch.setattr(mod, 'run', lambda args, *a, **kw: calls.append(args))
    isolated = mod.create_worktree('base')
    assert calls[0] == ['git', 'worktree', 'add', '--detach', str(isolated), 'base']
    assert (isolated / 'node_modules').is_symlink()
    assert (isolated / '.build-venv').is_symlink()
    assert not (isolated / 'foreign.json').exists()


def test_safe_shared_ff_preserves_foreign_bytes(mod, monkeypatch, tmp_path):
    shared = mod.ROOT
    shared.mkdir()
    foreign = shared / 'foreign.txt'
    foreign.write_bytes(b'foreign')
    snapshot = mod.hashes({'foreign.txt'})
    calls = []
    heads = iter(['base', 'candidate'])
    monkeypatch.setattr(mod, 'shared_gate', lambda base: next(heads))
    monkeypatch.setattr(mod, 'run', lambda args, *a, **kw: calls.append((mod.ROOT, args)))
    with mod.repository_root(tmp_path / 'isolated'):
        mod.integrate_shared(shared, 'base', 'candidate', snapshot)
        assert mod.ROOT != shared
    assert calls == [(shared, ['git', 'merge', '--ff-only', 'candidate'])]
    assert foreign.read_bytes() == b'foreign'


def test_history_membership_detects_producer_dropped_stock(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    (isolated / 'public/data/low-chip-history/2026-11-16.json').write_text(json.dumps({'intersection': ['600001.SH', '600002.SH']}))
    assert mod.main() != 0
    assert not any('scripts/build_site.py' in args for _, args in calls)


def test_industry_support_is_committed_only_after_enrichment(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    original = mod.run
    def enrich(args, *a, **kw):
        result = original(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            data = json.loads((isolated / TRACKING).read_text())
            del data['stocks']['600001.SH']['industry_source']
            (isolated / TRACKING).write_text(json.dumps(data))
        if 'scripts/refresh_low_chip_ftshare_industry.py' in args:
            (isolated / TRACKING).write_text(json.dumps(payload()))
            (isolated / CURRENT).write_text('{"industry_contract":"this run"}')
            (isolated / MAP).write_text('{"industry_cache":"this run"}')
            state['dirty'] |= {CURRENT, MAP}
        return result
    monkeypatch.setattr(mod, 'run', enrich)
    assert mod.main() == 0
    assert state['approved'] == {TRACKING, CURRENT, MAP}


def test_unchanged_rerun_requires_successful_producer_and_coverage(pipeline, monkeypatch):
    mod, calls, state, isolated = pipeline
    original = mod.run
    def unchanged(args, *a, **kw):
        result = original(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            state['dirty'] = set()
        return result
    monkeypatch.setattr(mod, 'run', unchanged)
    assert mod.main() == 0
    assert any('scripts/update_low_chip_tracking.py' in args for _, args in calls)
    assert any('pytest' in args for _, args in calls)
    assert not any(args[:2] == ['git', 'push'] for _, args in calls)

