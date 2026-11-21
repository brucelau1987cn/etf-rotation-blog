"""Offline, host-local recovery entrypoint tests; production I/O is blocked."""
import importlib.util
import os
import hashlib
import json
import io
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from hermes_test_support import require_local_script

SCRIPT = Path('/root/.hermes/skills/research/a-share-low-profit-stock-screen/scripts/low_chip_resume_release.py')
require_local_script(SCRIPT)


@pytest.fixture
def resume(monkeypatch):
    spec = importlib.util.spec_from_file_location('resume_release_test', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv('LOW_CHIP_TRADE_DATE', raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError(f'unmocked external boundary: {args}')
    monkeypatch.setattr(mod.m.subprocess, 'run', forbidden)
    monkeypatch.setattr(mod.m.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(mod.m.urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(mod.m.urllib.request, 'build_opener', forbidden)
    import socket
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    return mod


def test_explicit_trade_date_is_required_before_publisher_io(resume, monkeypatch):
    monkeypatch.setattr(resume.m, 'publish_lock', lambda: pytest.fail('lock entered without explicit date'))
    with pytest.raises(SystemExit):
        resume.main(['--input-backup', '/unused', '--expected-count', '520'])


def test_all_fifteen_main_release_test_files_are_included(resume):
    assert len(resume.TESTS) == 15
    assert 'tests/test_recalibrate_dragon_snapshots.py' in resume.TESTS


@pytest.fixture
def recovery(resume, tmp_path, monkeypatch):
    mod, m = resume, resume.m
    shared = tmp_path / 'shared'
    shared.mkdir()
    backup = tmp_path / 'backup'
    backup.mkdir()
    day = '2026-11-17'
    data = {'data_as_of': day, 'intersection': ['000001.SZ']}
    blobs = {
        m.DATA_RELATIVE: json.dumps(data).encode(),
        'public/data/low-chip-history/2026-11-17.json': json.dumps(data).encode(),
        'public/data/low-chip-history-index.json': json.dumps({'items': [{'date': day, 'intersection_count': 1}]}).encode(),
    }
    foreign = 'foreign.txt'
    for root in (shared, backup):
        for rel, blob in blobs.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
    (shared / foreign).write_bytes(b'precious')
    (backup / 'manifest.json').write_text(json.dumps({rel: {'sha256': hashlib.sha256(blob).hexdigest()} for rel, blob in blobs.items()}))
    monkeypatch.setattr(m, 'ROOT', shared)
    for name, rel in {'DATA': m.DATA_RELATIVE, 'TRACKING': 'public/data/low-chip-tracking.json',
                      'FUYAO_SHADOW': 'public/data/model-lab/low-chip-fuyao-shadow.json',
                      'FTSHARE_INDUSTRY_CACHE': 'public/data/model-lab/ftshare-sw-industry-map.json',
                      'INDEX': 'public/data/low-chip-history-index.json', 'HISTORY_DIR': 'public/data/low-chip-history',
                      'RISK_STAGING_SCRIPT': 'scripts/attach_low_chip_risks.py'}.items():
        monkeypatch.setattr(m, name, shared / rel)
    monkeypatch.setattr(m, 'RELEASE_RUNS', tmp_path / 'runs')
    monkeypatch.setattr(m, 'RELEASE_STATE', tmp_path / 'state.json')
    monkeypatch.setattr(m, 'publish_lock', nullcontext)
    monkeypatch.setattr(m, 'release_calendar_gate', lambda d: {'status': 'run'})
    monkeypatch.setattr(m, 'validate_payload', lambda p: {'trade_date': p['data_as_of'], 'final': 1})
    state = {'shared_head': 'base', 'origin': 'base', 'committed': False, 'staged': set(), 'events': []}
    pages_env = tmp_path / 'pages.env'
    pages_env.write_text('CLOUDFLARE_API_TOKEN=offline-secret\nCLOUDFLARE_ACCOUNT_ID=account\n')
    monkeypatch.setattr(mod, 'PAGES_ENV', pages_env, raising=False)
    deployment = {'id': 'exact-deployment', 'url': 'https://verified.etf-rotation-blog.pages.dev',
                  'environment': 'production', 'latest_stage': {'status': 'success'},
                  'deployment_trigger': {'metadata': {'commit_hash': 'candidate'}}}
    state['deployment'] = deployment
    state['api_urls'] = []
    def build_opener(handler):
        state['proxy'] = handler.proxies
        class Opener:
            def open(self, request, timeout):
                state['api_urls'].append(request.full_url)
                assert request.get_header('Authorization') == 'Bearer offline-secret'
                result = [deployment] if '?' in request.full_url else deployment
                return io.BytesIO(json.dumps({'success': True, 'result': result}).encode())
        return Opener()
    monkeypatch.setattr(mod.m.urllib.request, 'build_opener', build_opener)
    monkeypatch.setattr(m, 'staged', lambda: set() if m.ROOT == shared else state['staged'])
    monkeypatch.setattr(m, 'dirty', lambda: (set(blobs) | {foreign} if state['shared_head'] == 'base' and not state.get('restored') else {foreign}) if m.ROOT == shared else (set() if state['committed'] else set(blobs)))
    def runner(args, *a, **kw):
        state['events'].append((m.ROOT, args))
        output = ''
        if args[:2] == ['git', 'rev-parse']:
            output = state['origin'] if args[2] == 'origin/main' else state['shared_head'] if m.ROOT == shared else 'candidate' if state['committed'] else 'base'
        elif args[:2] == ['git', 'add']:
            state['staged'] = set(args[3:])
        elif args[:2] == ['git', 'commit']:
            state['committed'], state['staged'] = True, set()
        elif args[:3] == ['git', 'show', '-s']:
            output = 'base'
        elif args[:2] == ['git', 'diff-tree']:
            output = '\0'.join(blobs) + '\0'
        elif args == ['npm', 'run', 'build']:
            for rel in blobs:
                p = m.ROOT / 'dist' / rel.removeprefix('public/')
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((m.ROOT / rel).read_bytes())
        elif args[:2] == ['git', 'push']:
            state['origin'] = 'candidate'
        elif args[:2] == ['git', 'ls-files']:
            output = '\0'.join(blobs) + '\0'
        elif args[:2] == ['git', 'restore']:
            state['restored'] = True
            for rel in args[args.index('--') + 1:]:
                (shared / rel).unlink(missing_ok=True)
        elif args[:3] == ['git', 'merge', '--ff-only']:
            state['shared_head'] = args[3]
            for rel, body in blobs.items():
                (shared / rel).write_bytes(body)
        elif len(args) > 2 and args[1] == '-c' and 'release_pages(' in args[2]:
            output = 'Deployment complete! https://verified.etf-rotation-blog.pages.dev'
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(m, 'run', runner)
    def hashes(expected, revision=None):
        state['events'].append(('hash', revision))
        if revision is None:
            assert m.owned_hashes(set(expected)) == expected
    monkeypatch.setattr(m, 'verify_owned_hashes', hashes)
    def sync(day, count):
        state['events'].append(('d1', day))
        return 'token'
    monkeypatch.setattr(m, 'sync_d1_metrics', sync)
    monkeypatch.setattr(m, 'verify_d1_api', lambda *a: {'count': 1})
    monkeypatch.setattr(m, 'verify_history_index', lambda *a, **kw: {'dates': 1, 'rows': 1})
    return mod, state, shared, backup, blobs


def test_complete_recovery_uses_isolated_candidate_and_preserves_foreign(recovery):
    mod, state, shared, backup, blobs = recovery
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 0
    events = state['events']
    assert any(e[1][:4] == ['git', 'worktree', 'add', '--detach'] for e in events if isinstance(e[1], list))
    tests = next(args for _, args in events if isinstance(args, list) and '-m' in args and 'pytest' in args)
    assert all(t in tests for t in mod.TESTS)
    assert ('hash', '') in events and ('hash', 'candidate') in events
    assert (shared / 'foreign.txt').read_bytes() == b'precious'
    assert state['shared_head'] == 'candidate'
    assert json.loads(mod.m.RELEASE_STATE.read_text())['pushed'] is True
    assert all(root != shared for root, args in events if isinstance(args, list) and args[:2] in (['git', 'commit'], ['npm', 'run'], ['git', 'push']))


def test_input_backup_byte_mismatch_blocks_before_any_release(recovery):
    mod, state, shared, backup, blobs = recovery
    (shared / mod.m.DATA_RELATIVE).write_bytes(b'tampered')
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert not any(e[0] == 'd1' for e in state['events'])
    assert not state['committed']


@pytest.mark.parametrize('hazard', ['calendar', 'date', 'snapshot', 'index', 'validation', 'tests',
                                   'build', 'canonicalize', 'dist', 'index_hash', 'candidate_hash',
                                   'parents', 'paths', 'shared_head', 'shared_bytes', 'remote'])
def test_pre_d1_failures_have_no_remote_writes(recovery, monkeypatch, capsys, hazard):
    mod, state, shared, backup, blobs = recovery
    m = mod.m
    original = m.run
    def runner(args, *a, **kw):
        if hazard == 'tests' and '-m' in args and 'pytest' in args:
            raise RuntimeError('test gate')
        if hazard == 'build' and args == ['npm', 'run', 'build']:
            raise RuntimeError('build gate')
        if hazard == 'canonicalize' and len(args) > 2 and 'restore_tracked_public_files' in args[2]:
            raise RuntimeError('canonicalize gate')
        result = original(args, *a, **kw)
        if hazard == 'parents' and args[:3] == ['git', 'show', '-s']:
            result.stdout = 'base extra-parent'
        if hazard == 'paths' and args[:2] == ['git', 'diff-tree']:
            result.stdout += 'foreign.txt\0'
        if args[:2] == ['git', 'commit']:
            if hazard == 'shared_head':
                state['shared_head'] = 'concurrent'
            if hazard == 'shared_bytes':
                (shared / m.DATA_RELATIVE).write_bytes(b'concurrent')
            if hazard == 'remote':
                state['origin'] = 'concurrent'
        return result
    monkeypatch.setattr(m, 'run', runner)
    if hazard == 'calendar':
        monkeypatch.setattr(m, 'release_calendar_gate', lambda d: {'status': 'error'})
    if hazard == 'validation':
        monkeypatch.setattr(m, 'validate_payload', lambda p: (_ for _ in ()).throw(RuntimeError('validate gate')))
    if hazard in ('date', 'snapshot', 'index'):
        rel = m.DATA_RELATIVE if hazard == 'date' else 'public/data/low-chip-history/2026-11-17.json' if hazard == 'snapshot' else 'public/data/low-chip-history-index.json'
        obj = json.loads(blobs[rel])
        if hazard == 'index':
            obj['items'][0]['intersection_count'] = 999
        else:
            obj['data_as_of'] = '2026-11-16'
        body = json.dumps(obj).encode()
        (shared / rel).write_bytes(body)
        (backup / rel).write_bytes(body)
        manifest = json.loads((backup / 'manifest.json').read_text())
        manifest[rel]['sha256'] = hashlib.sha256(body).hexdigest()
        (backup / 'manifest.json').write_text(json.dumps(manifest))
    if hazard == 'dist':
        monkeypatch.setattr(m, 'verify_owned_dist', lambda p: (_ for _ in ()).throw(RuntimeError('dist gate')))
    if hazard in ('index_hash', 'candidate_hash'):
        original_hash = m.verify_owned_hashes
        def hashes(expected, revision=None):
            if revision == ('' if hazard == 'index_hash' else 'candidate'):
                raise RuntimeError('blob gate')
            return original_hash(expected, revision)
        monkeypatch.setattr(m, 'verify_owned_hashes', hashes)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert not any(e[0] == 'd1' for e in state['events'])
    assert not any(isinstance(args, list) and args[:2] == ['git', 'push'] for _, args in state['events'])
    assert 'RESUME_OK' not in capsys.readouterr().out
    assert (shared / 'foreign.txt').read_bytes() == b'precious'


@pytest.mark.parametrize('boundary', ['d1', 'readback', 'history', 'push', 'pages', 'pages_url', 'integrate'])
def test_post_d1_failure_preserves_real_release_state(recovery, monkeypatch, capsys, boundary):
    mod, state, shared, backup, blobs = recovery
    m = mod.m
    def fail(*a, **kw):
        raise RuntimeError(f'mock {boundary} failure')
    if boundary in ('d1', 'readback', 'history'):
        monkeypatch.setattr(m, {'d1': 'sync_d1_metrics', 'readback': 'verify_d1_api', 'history': 'verify_history_index'}[boundary], fail)
    if boundary == 'integrate':
        monkeypatch.setattr(mod, 'integrate_inputs', fail)
    original = m.run
    def runner(args, *a, **kw):
        if boundary == 'push' and args[:2] == ['git', 'push']:
            fail()
        if len(args) > 2 and args[1] == '-c' and 'release_pages(' in args[2]:
            if boundary == 'pages':
                fail()
            if boundary == 'pages_url':
                return subprocess.CompletedProcess(args, 0, 'complete without URL', '')
        return original(args, *a, **kw)
    monkeypatch.setattr(m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    released = json.loads(m.RELEASE_STATE.read_text())
    assert released['d1_sync_started'] is True
    assert released['d1_verified'] is (boundary not in ('d1', 'readback', 'history'))
    assert released['pushed'] is (boundary in ('pages', 'pages_url', 'integrate'))
    assert state['shared_head'] == 'base'
    assert all((shared / rel).read_bytes() == blob for rel, blob in blobs.items())
    assert list(m.RELEASE_RUNS.glob('resume-*/baseline.json'))
    assert not list(m.RELEASE_RUNS.glob('resume-*/result.json'))
    assert 'RESUME_OK' not in capsys.readouterr().out


def test_expected_count_is_bound_before_candidate_release(recovery):
    mod, state, shared, backup, blobs = recovery
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '520', '--input-backup', str(backup)]) == 1
    assert not state['committed']
    assert not any(e[0] == 'd1' for e in state['events'])


@pytest.mark.parametrize('overlap', [True, False])
def test_reconcile_is_disjoint_fast_forward_only(recovery, monkeypatch, overlap):
    mod, state, shared, backup, blobs = recovery
    state['origin'] = 'remote'
    original = mod.m.run
    def runner(args, *a, **kw):
        if args[:3] == ['git', 'merge', '--ff-only'] and args[3] == 'remote':
            state['shared_head'] = 'remote'
            state['events'].append((mod.m.ROOT, args))
            return subprocess.CompletedProcess(args, 0, '', '')
        result = original(args, *a, **kw)
        if args[:3] == ['git', 'diff', '--name-only']:
            result.stdout = (mod.m.DATA_RELATIVE if overlap else 'remote-only.txt') + '\0'
        return result
    monkeypatch.setattr(mod.m, 'run', runner)
    monkeypatch.setattr(mod.m, 'dirty', lambda: set(blobs) | {'foreign.txt'})
    if overlap:
        with pytest.raises(RuntimeError, match='overlap'):
            mod.reconcile_origin()
        assert state['shared_head'] == 'base'
    else:
        assert mod.reconcile_origin() == 'remote'
        assert state['shared_head'] == 'remote'
    assert (shared / 'foreign.txt').read_bytes() == b'precious'


def test_canonicalization_occurs_inside_quarantine(recovery, monkeypatch):
    mod, state, shared, backup, blobs = recovery
    quarantine = {'active': False}
    def begin():
        quarantine['active'] = True
        return {'fake': b'original'}
    def end(saved):
        assert quarantine['active']
        quarantine['active'] = False
    monkeypatch.setattr(mod.m, 'quarantine_external_dirty', begin)
    monkeypatch.setattr(mod.m, 'restore_quarantined', end)
    original = mod.m.run
    def runner(args, *a, **kw):
        if args == ['npm', 'run', 'build'] or len(args) > 2 and args[1] == '-c':
            assert quarantine['active']
        return original(args, *a, **kw)
    monkeypatch.setattr(mod.m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 0
    assert quarantine['active'] is False


def test_candidate_tamper_during_final_fetch_blocks_before_d1(recovery, monkeypatch):
    mod, state, shared, backup, blobs = recovery
    original = mod.m.run
    def runner(args, *a, **kw):
        result = original(args, *a, **kw)
        if args == ['git', 'fetch', 'origin', 'main'] and state['committed']:
            (mod.m.ROOT / mod.m.DATA_RELATIVE).write_bytes(b'tampered-after-candidate-gate')
        return result
    monkeypatch.setattr(mod.m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert not any(e[0] == 'd1' for e in state['events'])


@pytest.mark.parametrize('hazard', ['traversal', 'backup_hash', 'scope', 'pending'])
def test_invalid_recovery_provenance_fails_before_candidate(recovery, monkeypatch, hazard):
    mod, state, shared, backup, blobs = recovery
    manifest = json.loads((backup / 'manifest.json').read_text())
    if hazard == 'traversal':
        manifest['../escape'] = {'sha256': 'a' * 64}
    if hazard == 'backup_hash':
        (backup / mod.m.DATA_RELATIVE).write_bytes(b'tampered')
    if hazard == 'scope':
        manifest.pop(mod.m.DATA_RELATIVE)
    if hazard == 'pending':
        mod.m.RELEASE_STATE.write_text(json.dumps({'d1_sync_started': True, 'pushed': False}))
    (backup / 'manifest.json').write_text(json.dumps(manifest))
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert not state['committed']
    assert not any(e[0] == 'd1' for e in state['events'])


def test_argument_environment_date_conflict_is_rejected(resume, monkeypatch):
    monkeypatch.setenv('LOW_CHIP_TRADE_DATE', '2026-11-16')
    with pytest.raises(SystemExit):
        resume.parse_args(['--trade-date', '2026-11-17', '--expected-count', '520', '--input-backup', '/unused'])


def test_integration_head_change_during_tracked_lookup_preserves_owned_inputs(recovery, monkeypatch):
    mod, state, shared, backup, blobs = recovery
    original = mod.m.run
    def runner(args, *a, **kw):
        result = original(args, *a, **kw)
        if args[:2] == ['git', 'ls-files']:
            state['shared_head'] = 'concurrent'
        return result
    monkeypatch.setattr(mod.m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert not any(isinstance(args, list) and args[:2] == ['git', 'restore'] for _, args in state['events'])
    assert all((shared / rel).read_bytes() == body for rel, body in blobs.items())


def test_integration_ff_failure_refills_verified_run_inputs(recovery, monkeypatch, capsys):
    mod, state, shared, backup, blobs = recovery
    original = mod.m.run
    def runner(args, *a, **kw):
        if args[:3] == ['git', 'merge', '--ff-only']:
            assert state['restored']
            assert all(not (shared / rel).exists() for rel in blobs)
            raise RuntimeError('simulated ff failure after restore')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod.m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert state['shared_head'] == 'base'
    assert all((shared / rel).read_bytes() == body for rel, body in blobs.items())
    assert (shared / 'foreign.txt').read_bytes() == b'precious'
    out = capsys.readouterr().out
    assert 'run inputs refilled' in out
    assert 'snapshot=' in out
    assert str(backup) in out


def test_detached_pages_has_explicit_metadata_and_exact_production_readback(recovery, monkeypatch):
    mod, state, shared, backup, blobs = recovery
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:8899')
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 0
    assert state['api_urls'] == [
        'https://api.cloudflare.com/client/v4/accounts/account/pages/projects/etf-rotation-blog/deployments?per_page=25',
        'https://api.cloudflare.com/client/v4/accounts/account/pages/projects/etf-rotation-blog/deployments/exact-deployment',
    ]
    assert state['proxy']['https'] == 'http://127.0.0.1:8899'
    code = next(args[2] for _, args in state['events'] if isinstance(args, list) and len(args) > 2
                and args[1] == '-c' and 'release_pages(' in args[2])
    calls = []
    import sys
    import types
    fake = types.ModuleType('scripts.pages_release')
    def release_pages(probes):
        subprocess.run(['npx', 'pages', 'deploy', 'dist'])
        return 'Deployment complete! https://verified.etf-rotation-blog.pages.dev'
    setattr(fake, 'release_pages', release_pages)
    monkeypatch.setitem(sys.modules, 'scripts.pages_release', fake)
    monkeypatch.setattr(subprocess, 'run', lambda args, *a, **kw: calls.append(args))
    # Exercise the actual child program while every external boundary stays mocked.
    with monkeypatch.context():
        exec(code, {})
    assert calls == [['npx', 'pages', 'deploy', 'dist', '--branch', 'main', '--commit-hash', 'candidate']]


@pytest.mark.parametrize('hazard', ['head', 'index', 'owned', 'candidate', 'candidate_dirty'])
def test_integration_ff_failure_retains_concurrent_or_candidate_state(recovery, monkeypatch, capsys, hazard):
    mod, state, shared, backup, blobs = recovery
    original = mod.m.run
    def runner(args, *a, **kw):
        if args[:3] == ['git', 'merge', '--ff-only']:
            assert state['restored']
            if hazard == 'head':
                state['shared_head'] = 'concurrent'
            if hazard == 'index':
                state['shared_index'] = {'concurrent.txt'}
            if hazard in ('owned', 'candidate_dirty'):
                (shared / mod.m.DATA_RELATIVE).write_bytes(b'concurrent-owned')
            if hazard in ('candidate', 'candidate_dirty'):
                if hazard == 'candidate':
                    original(args, *a, **kw)
                state['shared_head'] = 'candidate'
            raise RuntimeError('ff returned failure')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod.m, 'run', runner)
    original_staged = mod.m.staged
    monkeypatch.setattr(mod.m, 'staged', lambda: state.get('shared_index', set())
                        if mod.m.ROOT == shared else original_staged())
    original_dirty = mod.m.dirty
    monkeypatch.setattr(mod.m, 'dirty', lambda: original_dirty() | ({mod.m.DATA_RELATIVE}
                        if mod.m.ROOT == shared and (shared / mod.m.DATA_RELATIVE).exists()
                        and (shared / mod.m.DATA_RELATIVE).read_bytes() == b'concurrent-owned' else set()))
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    out = capsys.readouterr().out
    assert 'snapshot=' in out and str(backup) in out
    assert 'RESUME_OK' not in out
    if hazard == 'candidate':
        assert all((shared / rel).read_bytes() == body for rel, body in blobs.items())
        assert 'candidate verified and retained' in out
    elif hazard in ('owned', 'candidate_dirty'):
        assert (shared / mod.m.DATA_RELATIVE).read_bytes() == b'concurrent-owned'
    else:
        assert all(not (shared / rel).exists() for rel in blobs)
    assert (shared / 'foreign.txt').read_bytes() == b'precious'


@pytest.mark.parametrize('hazard', ['commit', 'environment', 'stage', 'url', 'id', 'absent', 'api_error', 'transport'])
def test_exact_pages_readback_failure_blocks_shared_integration(recovery, monkeypatch, capsys, hazard):
    mod, state, shared, backup, blobs = recovery
    def build_opener(handler):
        class Opener:
            def open(self, request, timeout):
                state['api_urls'].append(request.full_url)
                if hazard == 'transport':
                    raise OSError('offline-secret must be redacted')
                detail = json.loads(json.dumps(state['deployment']))
                if '?' in request.full_url:
                    result = [] if hazard == 'absent' else [detail]
                else:
                    if hazard == 'commit':
                        detail['deployment_trigger']['metadata']['commit_hash'] = 'other'
                    if hazard == 'environment':
                        detail['environment'] = 'preview'
                    if hazard == 'stage':
                        detail['latest_stage']['status'] = 'failure'
                    if hazard == 'url':
                        detail['url'] = 'https://other.etf-rotation-blog.pages.dev'
                    if hazard == 'id':
                        detail['id'] = 'other'
                    result = detail
                return io.BytesIO(json.dumps({'success': hazard != 'api_error', 'result': result}).encode())
        return Opener()
    monkeypatch.setattr(mod.m.urllib.request, 'build_opener', build_opener)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert state['shared_head'] == 'base'
    assert all((shared / rel).read_bytes() == body for rel, body in blobs.items())
    assert not any(isinstance(args, list) and args[:2] == ['git', 'restore'] for _, args in state['events'])
    assert json.loads(mod.m.RELEASE_STATE.read_text())['pushed'] is True
    out = capsys.readouterr().out
    assert 'offline-secret' not in out and 'RESUME_OK' not in out


def test_integration_refill_requires_base_bytes_even_with_clean_status(recovery, monkeypatch, capsys):
    mod, state, shared, backup, blobs = recovery
    original = mod.m.run
    def runner(args, *a, **kw):
        if args[:3] == ['git', 'merge', '--ff-only']:
            (shared / mod.m.DATA_RELATIVE).write_bytes(b'concurrent-base-drift')
            raise RuntimeError('ff failed')
        if args[:4] == ['git', 'diff', '--quiet', 'base']:
            state['base_diff_checked'] = True
            raise RuntimeError('base byte comparison failed')
        return original(args, *a, **kw)
    monkeypatch.setattr(mod.m, 'run', runner)
    assert mod.main(['--trade-date', '2026-11-17', '--expected-count', '1', '--input-backup', str(backup)]) == 1
    assert state['base_diff_checked']
    assert (shared / mod.m.DATA_RELATIVE).read_bytes() == b'concurrent-base-drift'
    assert 'manual recovery required' in capsys.readouterr().out


def test_refill_restores_untracked_input_and_owned_deletion(recovery, monkeypatch):
    mod, state, shared, backup, blobs = recovery
    deleted = 'public/data/low-chip-tracking.json'
    untracked = 'public/data/low-chip-history/2026-11-17.json'
    changed = set(blobs) | {deleted}
    owned = mod.m.owned_hashes(mod.m.release_paths())
    foreign = mod.path_hashes(shared, {'foreign.txt'})
    original = mod.m.run
    original_dirty = mod.m.dirty
    monkeypatch.setattr(mod.m, 'dirty', lambda: original_dirty() | ({deleted} if not state.get('restored') else set()))
    def runner(args, *a, **kw):
        if args[:2] == ['git', 'ls-files']:
            return subprocess.CompletedProcess(args, 0, '\0'.join(changed - {untracked}) + '\0', '')
        result = original(args, *a, **kw)
        if args[:2] == ['git', 'restore']:
            (shared / deleted).write_bytes(b'base tracking bytes')
        if args[:3] == ['git', 'merge', '--ff-only']:
            raise AssertionError('merge should be intercepted')
        return result
    def fail_merge(args, *a, **kw):
        if args[:3] == ['git', 'merge', '--ff-only']:
            assert not (shared / untracked).exists()
            assert (shared / deleted).read_bytes() == b'base tracking bytes'
            raise RuntimeError('ff failed')
        return runner(args, *a, **kw)
    monkeypatch.setattr(mod.m, 'run', fail_merge)
    with pytest.raises(RuntimeError, match='run inputs refilled'):
        mod.integrate_inputs(shared, 'base', 'candidate', owned, foreign, changed)
    assert not (shared / deleted).exists()
    assert all((shared / rel).read_bytes() == blob for rel, blob in blobs.items())

