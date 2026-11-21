"""Offline regression contract for the independently scheduled tracking publisher."""
import contextlib
import datetime
import importlib.util
import io
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

PUBLISHER = Path('/root/.hermes/scripts/update_low_chip_tracking_and_release.py')
DAY = '2026-11-17'
D1_COMMIT = 'a' * 40
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


def snapshot(day=DAY, intersection=None):
    return {'schema_version': 'a-low-chip-history-v1', 'data_as_of': day,
            'intersection': [] if intersection is None else intersection}


@pytest.mark.parametrize('data', [
    None,
    snapshot('2026-11-16', ['600001.SH']),
    snapshot(DAY, ['600001.SH', '600001.SH']),
    snapshot(DAY, ['not-a-stock-code']),
    {'schema_version': 'a-low-chip-history-v1', 'data_as_of': DAY},
])
def test_target_snapshot_contract_blocks_missing_wrong_day_or_fake_full_coverage(mod, tmp_path, data):
    mod.ROOT.mkdir(parents=True, exist_ok=True)
    baseline = mod.ROOT / f'public/data/low-chip-history/{DAY}.json'
    if data is not None:
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps(data))

    def git_show(args, *a, **kw):
        if args[:3] == ['git', 'show', f'base:public/data/low-chip-history/{DAY}.json']:
            if data is None:
                raise RuntimeError('missing committed snapshot')
            return subprocess.CompletedProcess(args, 0, baseline.read_bytes(), b'')
        raise AssertionError(args)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, 'run', git_show)
    try:
        with pytest.raises(RuntimeError):
            mod.validate_baseline_snapshot('base', DAY)
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize('day', ['20261117', '2026-W47-2', '２０２６-１１-１７',
                                 '2026-11-7', '2026-02-30', '2026-11-17\n', '../2026-11-17'])
def test_target_snapshot_rejects_invalid_day_before_git_lookup(mod, monkeypatch, day):
    def unexpected_git(*args, **kwargs):
        pytest.fail('invalid day reached Git lookup')
    monkeypatch.setattr(mod, 'run', unexpected_git)
    with pytest.raises(RuntimeError, match='invalid target day'):
        mod.validate_baseline_snapshot('base', day)


def test_target_snapshot_contract_allows_committed_empty_pool(mod, tmp_path):
    mod.ROOT.mkdir(parents=True, exist_ok=True)
    path = mod.ROOT / f'public/data/low-chip-history/{DAY}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(snapshot()).encode())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, 'run', lambda args, *a, **kw: subprocess.CompletedProcess(
        args, 0, path.read_bytes(), b''))
    try:
        result = mod.validate_baseline_snapshot('base', DAY)
        assert result['data_as_of'] == DAY and result['intersection'] == []
    finally:
        monkeypatch.undo()


def test_target_snapshot_contract_allows_committed_positive_pool(mod, tmp_path):
    mod.ROOT.mkdir(parents=True, exist_ok=True)
    path = mod.ROOT / f'public/data/low-chip-history/{DAY}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(snapshot(DAY, ['000001.SZ', '600001.SH'])).encode())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, 'run', lambda args, *a, **kw: subprocess.CompletedProcess(
        args, 0, path.read_bytes(), b''))
    try:
        result = mod.validate_baseline_snapshot('base', DAY)
        assert result['intersection'] == ['000001.SZ', '600001.SH']
    finally:
        monkeypatch.undo()


def test_target_snapshot_contract_blocks_worktree_bytes_not_matching_git_blob(mod, tmp_path):
    mod.ROOT.mkdir(parents=True, exist_ok=True)
    path = mod.ROOT / f'public/data/low-chip-history/{DAY}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    committed = json.dumps(snapshot(DAY, ['000001.SZ'])).encode()
    path.write_bytes(committed + b'\nworking-tree-tamper')
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod, 'run', lambda args, *a, **kw: subprocess.CompletedProcess(
        args, 0, committed, b''))
    try:
        with pytest.raises(RuntimeError, match='committed baseline'):
            mod.validate_baseline_snapshot('base', DAY)
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize('mutation', ['missing', 'wrong-day', 'partial', 'foreign-ancestor', 'symbolic-ref', 'malformed'])
def test_new_run_requires_current_fully_verified_d1_receipt(mod, tmp_path, monkeypatch, mutation):
    receipt = tmp_path / 'receipt.json'
    value = {'trade_date': DAY, 'candidate_commit': D1_COMMIT,
             'd1_sync_started': True, 'd1_verified': True, 'pushed': True}
    if mutation == 'malformed':
        receipt.write_text('null')
    elif mutation != 'missing':
        if mutation == 'wrong-day':
            value['trade_date'] = '2026-11-16'
        elif mutation == 'partial':
            value['d1_verified'] = False
        elif mutation == 'foreign-ancestor':
            value['candidate_commit'] = 'b' * 40
        elif mutation == 'symbolic-ref':
            value['candidate_commit'] = 'HEAD'
        receipt.write_text(json.dumps(value))
    monkeypatch.setattr(mod, 'D1_RECEIPT', receipt)
    if mutation == 'foreign-ancestor':
        monkeypatch.setattr(mod, 'run', lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError('not an ancestor')))
    else:
        monkeypatch.setattr(mod, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '', ''))
    with pytest.raises(RuntimeError, match='receipt|D1|ancestor'):
        mod.validate_d1_receipt('base', DAY, {})


def test_new_run_accepts_current_fully_verified_ancestor_receipt(mod, tmp_path, monkeypatch):
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'trade_date': DAY, 'candidate_commit': D1_COMMIT,
                                   'd1_sync_started': True, 'd1_verified': True, 'pushed': True}))
    monkeypatch.setattr(mod, 'D1_RECEIPT', receipt)
    relative = f'public/data/low-chip-history/{DAY}.json'
    blob = json.dumps(snapshot()).encode()
    commands = []

    def receipt_git(args, *a, **kw):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, blob if kw.get('binary') else '', '')

    monkeypatch.setattr(mod, 'run', receipt_git)
    baseline = {'path': relative, 'blob': blob}
    assert mod.validate_d1_receipt('base', DAY, baseline)['candidate_commit'] == D1_COMMIT
    assert commands == [['git', 'merge-base', '--is-ancestor', D1_COMMIT, 'base'],
                        ['git', 'show', f'{D1_COMMIT}:{relative}']]


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
    data = payload({'600001.SH': completed, '600002.SH': record('2026-11-16', coverage={'status': 'halted', 'target_day': DAY, 'source': 'eastmoney_suspend_report', 'reason': '全日停牌'})})
    result = mod.validate_tracking_coverage(data, DAY, set(data['stocks']))
    assert result == {'stocks': 2, 'complete': 1, 'covered': 0, 'halted': 1, 'target_day': DAY}


@pytest.mark.parametrize('coverage', [
    {'status': 'halted', 'target_day': DAY, 'reason': '全日停牌'},
    {'status': 'halted', 'target_day': DAY, 'source': 'other', 'reason': '全日停牌'},
    {'status': 'halted', 'target_day': '2026-11-16', 'source': 'eastmoney_suspend_report', 'reason': '全日停牌'},
    {'status': 'halted', 'target_day': DAY, 'source': 'eastmoney_suspend_report', 'reason': ' '},
    {'status': 'unavailable', 'target_day': DAY, 'source': 'eastmoney_suspend_report', 'reason': 'no data'},
])
def test_coverage_unproven_halt_or_failure_blocks(mod, coverage):
    with pytest.raises(RuntimeError, match='600001.SH'):
        mod.validate_tracking_coverage(payload({'600001.SH': record('2026-11-16', coverage=coverage)}), DAY, {'600001.SH'})


def test_target_bar_takes_precedence_over_halt(mod):
    data = payload({'600001.SH': record(coverage={
        'status': 'halted', 'target_day': DAY, 'source': 'eastmoney_suspend_report', 'reason': '全日停牌',
    })})
    summary = mod.validate_tracking_coverage(data, DAY, {'600001.SH'})
    assert summary['covered'] == 1 and summary['halted'] == 0


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
    receipt = tmp_path / 'low-chip-d1-release.json'
    receipt.write_text(json.dumps({
        'trade_date': DAY, 'candidate_commit': D1_COMMIT,
        'd1_sync_started': True, 'd1_verified': True, 'pushed': True,
    }))
    monkeypatch.setattr(mod, 'D1_RECEIPT', receipt, raising=False)
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
        put(root, f'public/data/low-chip-history/{DAY}.json', snapshot(DAY, ['600001.SH']))
    state['receipt_snapshot'] = (shared / f'public/data/low-chip-history/{DAY}.json').read_bytes()
    def fake_run(args, timeout=900, **kwargs):
        calls.append((mod.ROOT, args))
        text = ''
        if state['fail'] and state['fail'] in ' '.join(args):
            raise RuntimeError('injected stage failure')
        if args[0] == 'git':
            op = args[1]
            if op == 'rev-parse':
                text = (state['head'] if mod.ROOT == isolated else state['shared_head']) if args[2] == 'HEAD' else state['remote']
            elif op == 'merge-base':
                text = ''
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
                state['message'] = '\n\n'.join(args[i + 1] for i, arg in enumerate(args) if arg == '-m')
            elif op == 'show' and '--format=%B' in args:
                text = (state['message'].split('\0', 1)[0] + '\n').encode()
            elif args[1:4] == ['--no-replace-objects', 'cat-file', 'commit']:
                text = ('tree ' + 'b' * 40 + '\nparent ' + 'c' * 40 + '\n\n' + state['message'] + '\n').encode()
            elif op == 'show' and '--format=%P' in args:
                text = 'base'
            elif op == 'show':
                revision, rel = args[-1].split(':', 1)
                if revision == D1_COMMIT:
                    text = state['receipt_snapshot']
                    if text is None:
                        raise RuntimeError('missing blob at D1 commit')
                elif revision == 'base':
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
    state['real_calendar'] = mod.calendar_target
    monkeypatch.setattr(mod, 'calendar_target', lambda: DAY)
    monkeypatch.setattr(mod, 'publish_lock', contextlib.nullcontext)
    state['real_deploy'] = mod.deploy_and_verify
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda candidate, digest: {'candidate_commit': candidate, 'pages_api': 'success'})
    def integrate(*args):
        real_integrate(*args)
        calls.append((shared, ['INTEGRATE']))
    monkeypatch.setattr(mod, 'integrate_shared', integrate)
    return mod, calls, state, isolated


def test_pipeline_rejects_unsourced_suspension_before_release_side_effects(pipeline, monkeypatch, capsys):
    mod, calls, state, isolated = pipeline
    run = mod.run
    deployed = []

    def inject(args, *a, **kw):
        result = run(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            bad_halt = {'status': 'halted', 'target_day': DAY, 'reason': '全日停牌'}
            (isolated / TRACKING).write_text(json.dumps(payload({'600001.SH': record('2026-11-16', coverage=bad_halt)})))
        return result

    monkeypatch.setattr(mod, 'run', inject)
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda *a: deployed.append(a) or {})
    assert mod.main() != 0
    assert 'tracking coverage missing' in capsys.readouterr().out
    assert not deployed and state['remote'] == state['shared_head'] == 'base'
    assert not any(a[:2] in (['git', 'add'], ['git', 'commit'], ['git', 'push'])
                   or 'scripts/build_site.py' in a or a == ['INTEGRATE'] for _, a in calls)


@pytest.mark.parametrize('mutation', ['missing', 'worktree-only', 'worktree-missing', 'wrong-day', 'non-ascii-code'])
def test_pipeline_bad_target_snapshot_blocks_before_producer_and_release_side_effects(pipeline, monkeypatch, capsys, mutation):
    mod, calls, state, isolated = pipeline
    relative = f'public/data/low-chip-history/{DAY}.json'
    if mutation in {'missing', 'worktree-only'}:
        (mod.ROOT / relative).unlink()
    if mutation in {'missing', 'worktree-missing'}:
        (isolated / relative).unlink()
    if mutation in {'wrong-day', 'non-ascii-code'}:
        data = (snapshot('2026-11-16', ['600001.SH']) if mutation == 'wrong-day'
                else snapshot(DAY, ['６００００１.SH']))
        for root in (mod.ROOT, isolated):
            (root / relative).write_text(json.dumps(data))
    deploy = mod.deploy_and_verify
    deploys = []
    def record_deploy(*args):
        deploys.append(args)
        return deploy(*args)
    monkeypatch.setattr(mod, 'deploy_and_verify', record_deploy)
    assert mod.main() != 0
    output = capsys.readouterr().out
    assert 'STAGING BLOCKER' in output and 'target snapshot' in output
    assert not deploys
    assert state['remote'] == state['shared_head'] == 'base'
    assert not any('scripts/update_low_chip_tracking.py' in args or
                   'scripts/refresh_low_chip_ftshare_industry.py' in args or
                   'scripts/build_site.py' in args or args[:2] in (['git', 'commit'], ['git', 'push'])
                   for _, args in calls)


def test_pipeline_stale_d1_receipt_blocks_before_producer(pipeline, capsys):
    mod, calls, state, _ = pipeline
    receipt = json.loads(mod.D1_RECEIPT.read_text())
    receipt['trade_date'] = '2026-11-16'
    mod.D1_RECEIPT.write_text(json.dumps(receipt))
    assert mod.main() != 0
    assert 'D1 receipt trade_date' in capsys.readouterr().out
    assert state['remote'] == state['shared_head'] == 'base'
    assert not any('scripts/update_low_chip_tracking.py' in args for _, args in calls)


@pytest.mark.parametrize('mutation', ['absent', 'changed-membership', 'changed-bytes'])
def test_same_day_ancestor_receipt_must_cover_exact_snapshot(pipeline, monkeypatch, capsys, mutation):
    mod, calls, state, _ = pipeline
    if mutation == 'absent':
        state['receipt_snapshot'] = None
    elif mutation == 'changed-membership':
        state['receipt_snapshot'] = json.dumps(snapshot(DAY, ['000001.SZ'])).encode()
    else:
        state['receipt_snapshot'] += b'\n'
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda *a: pytest.fail('unexpected deploy'))

    assert mod.main() == 1
    assert 'D1 receipt snapshot' in capsys.readouterr().out
    assert state['remote'] == state['shared_head'] == 'base'
    assert not any(a[:2] in (['git', 'add'], ['git', 'commit'], ['git', 'push'], ['git', 'merge'])
                   or a == ['INTEGRATE'] or a[0] == mod.COLLECT_PYTHON or 'pytest' in a
                   for _, a in calls)


def test_producer_gets_previous_completed_session_chosen_by_gate(pipeline, monkeypatch):
    mod, calls, state, _ = pipeline

    class Morning(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 18, 9, tzinfo=tz)

    monkeypatch.setattr(datetime, 'datetime', Morning)
    calendar = ModuleType('scripts.check_a_share_cron_gate')
    dates = []

    def trading_day(day):
        dates.append(day)
        assert day in {'2026-11-18', DAY}
        return True, 'fixture exchange calendar'

    calendar.__dict__['is_trading_day'] = trading_day
    monkeypatch.setitem(sys.modules, calendar.__name__, calendar)
    monkeypatch.setattr(mod, 'calendar_target', state['real_calendar'])
    run = mod.run

    def execute_calendar_child(args, *a, **kw):
        if args[:2] == [mod.COLLECT_PYTHON, '-c'] and 'is_trading_day' in args[-1]:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exec(args[-1], {})  # Publisher's in-memory calendar script, no external process.
            return subprocess.CompletedProcess(args, 0, output.getvalue(), '')
        return run(args, *a, **kw)

    monkeypatch.setattr(mod, 'run', execute_calendar_child)
    assert mod.main() == 0
    assert dates == ['2026-11-18', DAY]
    producer = next(args for _, args in calls if 'scripts/update_low_chip_tracking.py' in args)
    assert producer[2:] == ['--target-day', DAY]


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


def test_commit_hook_cannot_change_target_before_push(pipeline, monkeypatch, capsys):
    mod, calls, state, _ = pipeline
    run = mod.run
    def hook(args, *a, **kw):
        result = run(args, *a, **kw)
        if args[:2] == ['git', 'commit']:
            state['message'] = 'Fixture candidate\n\nTracking-Target-Day: 2026-11-16'
        return result
    monkeypatch.setattr(mod, 'run', hook)
    assert mod.main() == 1
    assert 'candidate target day' in capsys.readouterr().out
    assert state['remote'] == state['shared_head'] == 'base'
    assert not any(a[:2] == ['git', 'push'] or a == ['INTEGRATE'] for _, a in calls)


def test_postpush_deploy_failure_retains_candidate_without_shared_integration(pipeline, monkeypatch, capsys):
    mod, calls, _, _ = pipeline
    def failed(*args):
        raise RuntimeError('Pages verification unavailable')
    monkeypatch.setattr(mod, 'deploy_and_verify', failed)
    assert mod.main() != 0
    assert any(args[:2] == ['git', 'push'] for _, args in calls)
    assert not any(args == ['INTEGRATE'] for _, args in calls)
    assert '"pushed": true' in capsys.readouterr().out


def test_resume_uses_committed_target_when_all_stocks_are_halted(pipeline, monkeypatch, capsys):
    mod, calls, state, isolated = pipeline
    run = mod.run
    def collect_halts(args, *a, **kw):
        result = run(args, *a, **kw)
        if 'scripts/update_low_chip_tracking.py' in args:
            halt = {'status': 'halted', 'target_day': DAY, 'source': 'eastmoney_suspend_report',
                    'reason': '全日停牌'}
            old = '2026-11-16'
            data = payload({'600001.SH': record(old, coverage=halt)})
            data['data_through'] = old
            (isolated / TRACKING).write_text(json.dumps(data))
        return result
    monkeypatch.setattr(mod, 'run', collect_halts)
    accept = mod.deploy_and_verify
    def fail_deploy(*args):
        raise RuntimeError('Pages unavailable after push')
    monkeypatch.setattr(mod, 'deploy_and_verify', fail_deploy)
    assert mod.main() == 1
    assert state['remote'] == 'candidate' and state['shared_head'] == 'base'
    capsys.readouterr()

    # The retained commit is the only authority for the selected day.
    mod.D1_RECEIPT.unlink()
    monkeypatch.setattr(mod, 'calendar_target', lambda: pytest.fail('resume must not rerun calendar'))
    monkeypatch.setattr(mod, 'deploy_and_verify', accept)
    calls.clear()
    assert mod.main(['--resume-worktree', str(isolated)]) == 0
    assert state['shared_head'] == 'candidate'
    published = next(json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{'))
    assert published['coverage']['target_day'] == DAY
    assert published['coverage']['halted'] == 1
    assert any('pytest' in a for _, a in calls)
    assert all('scripts/update_low_chip_tracking.py' not in a and a[:2] not in (['git', 'commit'], ['git', 'push'])
               for _, a in calls)


@pytest.mark.parametrize('trailer', ['', f'Tracking-Target-Day: {DAY}\nTracking-Target-Day: {DAY}',
                                    f'Tracking-Target-Day: {DAY}\ntracking-target-day: 2026-11-16',
                                    'Tracking-Target-Day: 20261117', 'Tracking-Target-Day: 2026-02-30',
                                    f'Tracking-Target-Day: {DAY} ', f'Tracking-Target-Day: {DAY}\r',
                                    f'Tracking-Target-Day: {DAY}\n\nNot a final trailer',
                                    f'Tracking-Target-Day: {DAY}\n\0'])
def test_resume_blocks_untrusted_commit_target_before_downstream_actions(pipeline, monkeypatch, capsys, trailer):
    mod, calls, state, isolated = pipeline
    def fail_deploy(*args):
        raise RuntimeError('Pages unavailable after push')
    monkeypatch.setattr(mod, 'deploy_and_verify', fail_deploy)
    assert mod.main() == 1
    capsys.readouterr()

    state['message'] = 'Fixture candidate\n\n' + trailer
    calls.clear()
    monkeypatch.setattr(mod, 'deploy_and_verify', lambda *a: pytest.fail('unexpected resume deploy'))
    assert mod.main(['--resume-worktree', str(isolated)]) == 1
    assert 'candidate target day' in capsys.readouterr().out
    assert state['remote'] == 'candidate' and state['shared_head'] == 'base'
    assert all(a[0] == 'git' and a[1] not in {'add', 'commit', 'push', 'merge'} for _, a in calls)


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
    # Resume uses the already-pushed candidate after the singleton is replaced.
    mod.D1_RECEIPT.unlink()
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
    'candidate-dirty', 'candidate-staged', 'parent', 'paths', 'source', 'target-snapshot',
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
    elif mutation == 'target-snapshot':
        (isolated / f'public/data/low-chip-history/{DAY}.json').unlink()
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

