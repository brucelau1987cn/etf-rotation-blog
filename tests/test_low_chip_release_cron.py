import importlib.util
import json
import os
import subprocess
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path

import pytest


SCRIPT = Path('/root/.hermes/scripts/update_low_chip_and_release.py')
LOCAL_HERMES_READABLE = (
    os.environ.get('CI', '').lower() != 'true'
    and os.access(SCRIPT, os.R_OK)
)
pytestmark = pytest.mark.skipif(
    not LOCAL_HERMES_READABLE,
    reason='requires readable local Hermes release script',
)


def load_module():
    spec = importlib.util.spec_from_file_location('low_chip_release_cron', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_calendar_gate_skips_closed_days_and_fails_closed_when_unavailable():
    module = load_module()
    assert module.release_calendar_gate("2026-09-13", lookup=lambda day: (False, "fixture")) == {
        "status": "skip", "trade_date": "2026-09-13", "calendar_source": "fixture",
    }
    assert module.release_calendar_gate("2026-09-14", lookup=lambda day: (True, "fixture"))["status"] == "run"
    assert module.release_calendar_gate("2026-09-14", lookup=lambda day: (None, "unavailable"))["status"] == "error"


def test_sync_d1_metrics_requires_complete_insert_count(tmp_path):
    module = load_module()
    env_file = tmp_path / 'sync.env'
    env_file.write_text('LOW_CHIP_SYNC_TOKEN=test-token\n', encoding='utf-8')
    calls = []

    def runner(args, timeout=900, env=None, **kwargs):
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0, 'D1 sync: 10/10 inserted (2026-08-14)\n', '')

    token = module.sync_d1_metrics('2026-08-14', 10, runner=runner, env_file=env_file)
    assert token == 'test-token'
    assert calls[0][0][-2:] == ['--day', '2026-08-14']
    assert calls[0][1]['LOW_CHIP_SYNC_TOKEN'] == 'test-token'


def test_verify_d1_api_uses_bearer_and_requires_expected_rows(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path)
    (tmp_path / '2026-08-14.json').write_text(json.dumps({
        'data_as_of': '2026-08-14', 'intersection': [f'{i:06d}.SZ' for i in range(10)],
    }), encoding='utf-8')
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                'ok': True,
                'trade_date': '20260814',
                'count': 10,
                'results': [{'stock_code': f'{i:06d}'} for i in range(10)],
            }).encode()

    def opener(request, timeout=30):
        captured['authorization'] = request.get_header('Authorization')
        captured['url'] = request.full_url
        return Response()

    result = module.verify_d1_api('2026-08-14', 10, 'test-token', opener=opener)
    assert result['count'] == 10
    assert captured['authorization'] == 'Bearer test-token'
    assert 'date=2026-08-14' in captured['url']


def test_verify_history_index_checks_every_published_date(tmp_path):
    module = load_module()
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'items': [
        {'date': '2026-08-14', 'intersection_count': 10},
        {'date': '2026-08-13', 'intersection_count': 4},
        {'date': '2026-08-07', 'intersection_count': 0},
    ]}), encoding='utf-8')
    calls = []

    def verifier(day, count, token):
        calls.append((day, count, token))
        return {'ok': True, 'trade_date': day.replace('-', ''), 'count': count, 'results': [{}] * count}

    result = module.verify_history_index('test-token', index_file=index, verifier=verifier)
    assert result == {'dates': 3, 'rows': 14}
    assert calls == [
        ('2026-08-14', 10, 'test-token'),
        ('2026-08-13', 4, 'test-token'),
        ('2026-08-07', 0, 'test-token'),
    ]


def test_release_state_records_auditable_d1_push_transitions(tmp_path):
    module = load_module()
    state_file = tmp_path / 'release-state.json'
    module.write_release_state({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }, state_file=state_file)

    assert json.loads(state_file.read_text(encoding='utf-8')) == {
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
        'trade_date': '2026-09-18',
    }


def test_release_state_fsyncs_parent_directory_after_atomic_replace(tmp_path, monkeypatch):
    module = load_module()
    state_file = tmp_path / 'state' / 'release-state.json'
    real_open = module.os.open
    real_fsync = module.os.fsync
    real_replace = module.os.replace
    directory_fds = set()
    fsynced = []
    events = []

    def tracking_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & getattr(module.os, 'O_DIRECTORY', 0):
            directory_fds.add(fd)
        return fd

    def tracking_fsync(fd):
        fsynced.append(fd)
        if fd in directory_fds:
            events.append('directory-fsync')
        return real_fsync(fd)

    def tracking_replace(source, destination):
        events.append('replace')
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, 'open', tracking_open)
    monkeypatch.setattr(module.os, 'fsync', tracking_fsync)
    monkeypatch.setattr(module.os, 'replace', tracking_replace)
    module.write_release_state({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }, state_file=state_file)

    assert directory_fds
    assert directory_fds <= set(fsynced)
    assert events.index('replace') < events.index('directory-fsync')


def test_pending_d1_release_verifies_and_retries_only_push(tmp_path, monkeypatch, capsys):
    module = load_module()
    state_file = tmp_path / 'release-state.json'
    state_file.write_text(json.dumps({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }), encoding='utf-8')
    calls = []
    pushed = False

    def fake_run(args, timeout=900, env=None, **kwargs):
        nonlocal pushed
        calls.append(args)
        if args == ['git', 'push', 'origin', 'candidate-sha:main']:
            pushed = True
            return subprocess.CompletedProcess(args, 0, '', '')
        if args == ['git', 'rev-parse', 'origin/main']:
            head = 'candidate-sha' if pushed else 'base-sha'
            return subprocess.CompletedProcess(args, 0, head + '\n', '')
        if args == ['git', 'rev-parse', 'candidate-sha^']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args[:2] == ['git', 'show']:
            payload = {'data_as_of': '2026-09-18', 'intersection': ['000001.SZ', '000002.SZ']}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'load_env_file', lambda *_args, **_kwargs: {'LOW_CHIP_SYNC_TOKEN': 'token'})
    monkeypatch.setattr(module, 'verify_d1_api', lambda day, count, token: {
        'ok': True,
        'count': count,
        'results': [{'stock_code': '000001'}, {'stock_code': '000002'}],
    })

    assert module.recover_pending_release(state_file=state_file) == 0
    saved = json.loads(state_file.read_text(encoding='utf-8'))
    assert saved['d1_verified'] is True
    assert saved['pushed'] is True
    assert ['git', 'push', 'origin', 'candidate-sha:main'] in calls
    assert not any('build_low_chip_base.py' in call for call in calls)
    assert 'RECOVERY COMPLETE' in capsys.readouterr().out


def test_pending_d1_release_blocks_same_count_with_different_stock_codes(tmp_path, monkeypatch, capsys):
    module = load_module()
    state_file = tmp_path / 'release-state.json'
    state_file.write_text(json.dumps({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }), encoding='utf-8')
    calls = []

    def fake_run(args, timeout=900, env=None, **kwargs):
        calls.append(args)
        if args == ['git', 'rev-parse', 'origin/main']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args == ['git', 'rev-parse', 'candidate-sha^']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args[:2] == ['git', 'show']:
            payload = {'data_as_of': '2026-09-18', 'intersection': ['000001.SZ', '000002.SZ']}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'load_env_file', lambda *_args, **_kwargs: {'LOW_CHIP_SYNC_TOKEN': 'token'})
    monkeypatch.setattr(module, 'verify_d1_api', lambda day, count, token: {
        'ok': True,
        'count': count,
        'results': [{'stock_code': '000001'}, {'stock_code': '000003'}],
    })

    assert module.recover_pending_release(state_file=state_file) == 1
    assert not any(call[:2] == ['git', 'push'] for call in calls)
    saved = json.loads(state_file.read_text(encoding='utf-8'))
    assert saved['d1_verified'] is False
    assert saved['pushed'] is False
    assert 'stock code set mismatch' in capsys.readouterr().out


def test_pending_release_with_candidate_on_origin_still_requires_d1_verification(tmp_path, monkeypatch, capsys):
    module = load_module()
    state_file = tmp_path / 'release-state.json'
    state_file.write_text(json.dumps({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }), encoding='utf-8')
    calls = []

    def fake_run(args, timeout=900, env=None, **kwargs):
        calls.append(args)
        if args == ['git', 'rev-parse', 'origin/main']:
            return subprocess.CompletedProcess(args, 0, 'candidate-sha\n', '')
        if args == ['git', 'rev-parse', 'candidate-sha^']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args[:2] == ['git', 'show']:
            payload = {'data_as_of': '2026-09-18', 'intersection': ['000001.SZ']}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'load_env_file', lambda *_args, **_kwargs: {'LOW_CHIP_SYNC_TOKEN': 'token'})
    monkeypatch.setattr(module, 'verify_d1_api', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('D1 unavailable')))

    assert module.recover_pending_release(state_file=state_file) == 1
    assert not any(call[:2] == ['git', 'push'] for call in calls)
    saved = json.loads(state_file.read_text(encoding='utf-8'))
    assert saved['d1_verified'] is False
    assert saved['pushed'] is False
    assert 'STAGING BLOCKER' in capsys.readouterr().out


def test_pending_d1_release_blocks_when_d1_cannot_be_verified(tmp_path, monkeypatch, capsys):
    module = load_module()
    state_file = tmp_path / 'release-state.json'
    state_file.write_text(json.dumps({
        'trade_date': '2026-09-18',
        'candidate_commit': 'candidate-sha',
        'd1_sync_started': True,
        'd1_verified': False,
        'pushed': False,
    }), encoding='utf-8')
    calls = []

    def fake_run(args, timeout=900, env=None, **kwargs):
        calls.append(args)
        if args == ['git', 'rev-parse', 'origin/main']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args == ['git', 'rev-parse', 'candidate-sha^']:
            return subprocess.CompletedProcess(args, 0, 'base-sha\n', '')
        if args[:2] == ['git', 'show']:
            payload = {'data_as_of': '2026-09-18', 'intersection': ['000001.SZ']}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), '')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'load_env_file', lambda *_args, **_kwargs: {'LOW_CHIP_SYNC_TOKEN': 'token'})
    monkeypatch.setattr(module, 'verify_d1_api', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('D1 unavailable')))

    assert module.recover_pending_release(state_file=state_file) == 1
    assert not any(call[:2] == ['git', 'push'] for call in calls)
    assert json.loads(state_file.read_text(encoding='utf-8'))['pushed'] is False
    assert 'STAGING BLOCKER' in capsys.readouterr().out


def test_pipeline_checks_pending_release_before_generation():
    source = SCRIPT.read_text(encoding='utf-8')
    recovery = source.index('recover_pending_release()')
    build = source.index("run([sys.executable, 'scripts/build_low_chip_base.py'")
    assert recovery < build


def test_risk_staging_gate_uses_copy_and_preserves_formal_file(tmp_path, monkeypatch):
    module = load_module()
    formal = tmp_path / 'formal.json'
    formal.write_text('{"data_as_of":"2026-09-18"}\n', encoding='utf-8')
    risk_script = tmp_path / 'attach.py'
    risk_script.write_text('# fixture\n', encoding='utf-8')
    monkeypatch.setattr(module, 'DATA', formal)
    monkeypatch.setattr(module, 'RISK_STAGING_SCRIPT', risk_script)
    before = formal.read_bytes()

    def runner(args, timeout=900, env=None, **kwargs):
        staging = Path(args[args.index('--data') + 1])
        assert staging != formal
        staging.write_text('{"staged":true}\n', encoding='utf-8')
        report = {"status": "dry-run", "coverage": {"requested": 1, "attached": 1, "failed": []}}
        return subprocess.CompletedProcess(args, 0, json.dumps(report) + '\n', '')

    report = module.run_risk_staging_gate(runner=runner)
    assert report['status'] == 'dry-run'
    assert formal.read_bytes() == before


def test_risk_staging_gate_fails_closed_on_incomplete_coverage(tmp_path, monkeypatch):
    module = load_module()
    formal = tmp_path / 'formal.json'
    formal.write_text('{}\n', encoding='utf-8')
    risk_script = tmp_path / 'attach.py'
    risk_script.write_text('# fixture\n', encoding='utf-8')
    monkeypatch.setattr(module, 'DATA', formal)
    monkeypatch.setattr(module, 'RISK_STAGING_SCRIPT', risk_script)
    report = {"status": "STAGING BLOCKER", "coverage": {"requested": 1, "attached": 0, "failed": ["000001.SZ"]}}
    runner = lambda args, timeout=900, env=None, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(report) + '\n', '')
    with pytest.raises(RuntimeError, match='risk staging gate failed after 3 attempts'):
        # 0 秒退避：避免 30+60=90 秒真实睡眠拖慢单测
        module.run_risk_staging_gate(runner=runner, sleeper=lambda _seconds: None)


def test_full_build_and_local_commit_precede_remote_side_effects():
    source = SCRIPT.read_text(encoding='utf-8')
    build = source.index("run(['npm', 'run', 'build']")
    commit = source.index("run(['git', 'commit'")
    sync = source.index("token = sync_d1_metrics(summary['trade_date'], summary['final'])")
    verify = source.index("d1 = verify_d1_api(summary['trade_date'], summary['final'], token)")
    history = source.index('history = verify_history_index(token)')
    push = source.index("run(['git', 'push'", history)
    release = source.index('release = run([sys.executable, \'-c\', release_code]')
    assert build < commit < sync < verify < history < push < release
    assert 'surgical-json-release' not in source
    assert "if 'US batch mismatch'" not in source


def test_fuyao_shadow_audit_runs_after_enrichment_without_changing_formal_gate():
    source = SCRIPT.read_text(encoding='utf-8')
    financials = source.index("run([sys.executable, 'scripts/attach_low_chip_financials.py']")
    shadow = source.index("run([sys.executable, 'scripts/audit_low_chip_fuyao.py', '--soft-fail']")
    archive = source.index("run([sys.executable, 'scripts/archive_low_chip_snapshot.py']")
    assert financials < shadow < archive
    assert "public/data/model-lab/low-chip-fuyao-shadow.json" in source
    assert "fuyao_shadow = json.loads(FUYAO_SHADOW.read_text(encoding='utf-8'))" in source
    assert 'fuyao_shadow.get("status", "N/A")' in source
    assert "'public/data/model-lab/low-chip-fuyao-shadow.json'" in source
    assert shadow < source.index("run(['npm', 'run', 'build']")


def test_ftshare_industry_refresh_runs_after_archive_and_before_final_archive() -> None:
    # tracking 已从主链路拆出（独立 cron 16:40 回填），但 refresh 仍必须在
    # 首次 archive 之后（快照里要带 refresh 前的对照字段）。
    source = SCRIPT.read_text(encoding='utf-8')
    first_archive = source.index("run([sys.executable, 'scripts/archive_low_chip_snapshot.py']")
    industry = source.index("run([sys.executable, 'scripts/refresh_low_chip_ftshare_industry.py']")
    industry_etf = source.index("run([sys.executable, 'scripts/attach_low_chip_industry_etfs.py']")
    final_archive = source.rindex("run([sys.executable, 'scripts/archive_low_chip_snapshot.py']")
    assert first_archive < industry < industry_etf < final_archive


def test_backup_restore_recovers_exact_pre_run_generated_state(tmp_path, monkeypatch):
    module = load_module()
    data_dir = tmp_path / 'data'
    history_dir = data_dir / 'history'
    history_dir.mkdir(parents=True)
    paths = {
        'DATA': data_dir / 'stocks.json',
        'TRACKING': data_dir / 'tracking.json',
        'FUYAO_SHADOW': data_dir / 'fuyao.json',
        'FTSHARE_INDUSTRY_CACHE': data_dir / 'ftshare-industry.json',
        'INDEX': data_dir / 'index.json',
    }
    originals = {
        'DATA': b'stocks-before',
        'TRACKING': b'tracking-before',
        'INDEX': b'index-before',
    }
    for name, path in paths.items():
        monkeypatch.setattr(module, name, path)
        if name in originals:
            path.write_bytes(originals[name])
    monkeypatch.setattr(module, 'HISTORY_DIR', history_dir)
    (history_dir / 'old.json').write_bytes(b'history-before')

    backup_dir = tmp_path / 'backup'
    state = module.backup_generated_state(backup_dir)
    for path in paths.values():
        path.write_bytes(b'changed')
    (history_dir / 'old.json').write_bytes(b'changed-history')
    (history_dir / 'new.json').write_bytes(b'new-history')

    module.restore_generated_state(backup_dir, state)

    for name, original in originals.items():
        assert paths[name].read_bytes() == original
    assert not paths['FUYAO_SHADOW'].exists()
    assert not paths['FTSHARE_INDUSTRY_CACHE'].exists()
    assert (history_dir / 'old.json').read_bytes() == b'history-before'
    assert not (history_dir / 'new.json').exists()


def test_history_restore_recovers_from_keyboard_interrupt(tmp_path, monkeypatch):
    module = load_module()
    history_dir = tmp_path / 'history'
    history_dir.mkdir()
    (history_dir / 'old.json').write_bytes(b'current-history')
    backup_dir = tmp_path / 'backup'
    (backup_dir / 'history').mkdir(parents=True)
    (backup_dir / 'history' / 'old.json').write_bytes(b'backup-history')
    monkeypatch.setattr(module, 'HISTORY_DIR', history_dir)
    real_replace = module.os.replace

    def interrupt_staged_swap(source, destination):
        source_path = Path(source)
        if source_path.name.startswith('.low-chip-history.restore-') and Path(destination) == history_dir:
            raise KeyboardInterrupt('forced interruption')
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, 'replace', interrupt_staged_swap)
    state = {'files': [], 'history_existed': True}
    with pytest.raises(KeyboardInterrupt, match='forced interruption'):
        module.restore_generated_state(backup_dir, state)

    assert history_dir.is_dir()
    assert (history_dir / 'old.json').read_bytes() == b'current-history'


def test_rollback_candidate_resets_head_index_and_restores_files(monkeypatch, tmp_path):
    module = load_module()
    calls = []
    state = {'sentinel': True}
    def fake_run(args, timeout=900, env=None, **kwargs):
        calls.append(args)
        output = 'base-sha\n' if args == ['git', 'rev-parse', 'HEAD'] else ''
        return subprocess.CompletedProcess(args, 0, output, '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'restore_generated_state', lambda path, got: calls.append(['restore', path, got]))
    monkeypatch.setattr(module, 'staged', lambda: set())
    monkeypatch.setattr(module, 'dirty', lambda: set(module.ALLOWED_DIRTY))

    module.rollback_candidate('base-sha', None, tmp_path, state)

    assert calls[0] == ['git', 'rev-parse', 'HEAD']
    assert calls[1] == ['git', 'reset', '--mixed', 'base-sha']
    assert calls[2] == ['restore', tmp_path, state]


def test_rollback_preserves_preexisting_foreign_dirty_paths(monkeypatch, tmp_path):
    module = load_module()
    foreign = {'scripts/publish_a_share_nightly.py', 'src/content/blog/2026-10-30.md'}
    monkeypatch.setattr(module, 'run', lambda args, timeout=900, env=None, **kwargs:
                        subprocess.CompletedProcess(args, 0, 'base-sha\n', ''))
    monkeypatch.setattr(module, 'restore_generated_state', lambda *_args: None)
    monkeypatch.setattr(module, 'staged', lambda: set())
    monkeypatch.setattr(module, 'dirty', lambda: foreign | set(module.ALLOWED_DIRTY))
    module.rollback_candidate('base-sha', None, tmp_path, {'dirty_paths': foreign})


def test_rollback_still_reports_new_owned_residue(monkeypatch, tmp_path):
    module = load_module()
    foreign = {'scripts/publish_a_share_nightly.py'}
    monkeypatch.setattr(module, 'run', lambda args, timeout=900, env=None, **kwargs:
                        subprocess.CompletedProcess(args, 0, 'base-sha\n', ''))
    monkeypatch.setattr(module, 'restore_generated_state', lambda *_args: None)
    monkeypatch.setattr(module, 'staged', lambda: set())
    monkeypatch.setattr(module, 'dirty', lambda: foreign | {module.DATA_RELATIVE})
    with pytest.raises(RuntimeError, match='rollback left dirty paths'):
        module.rollback_candidate('base-sha', None, tmp_path, {'dirty_paths': foreign})


def test_rollback_refuses_to_overwrite_concurrent_head(monkeypatch, tmp_path):
    module = load_module()
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, timeout=900, env=None, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, 'concurrent-sha\n', ''))

    with pytest.raises(RuntimeError, match='rollback refused: concurrent HEAD'):
        module.rollback_candidate('base-sha', 'candidate-sha', tmp_path, {'sentinel': True})

    assert ['git', 'reset', '--mixed', 'base-sha'] not in calls


def test_low_chip_pipeline_loads_rotation_pool_without_fixed_key(monkeypatch):
    module = load_module()
    monkeypatch.setenv('IWENCAI_APIKEYS', json.dumps(['key-high', 'key-regular']))
    monkeypatch.setenv('IWENCAI_API_KEY', 'stale-fixed-key')

    keys = module.configure_iwencai_rotation()

    assert keys == ['key-high', 'key-regular']
    assert module.os.environ['IWENCAI_APIKEYS'] == json.dumps(['key-high', 'key-regular'])
    assert 'IWENCAI_API_KEY' not in module.os.environ
    assert module.run.__defaults__[1] is None  # subprocesses inherit the pinned process environment


def test_low_chip_build_loads_rotation_pool_from_credentials_file(tmp_path, monkeypatch):
    module = load_module()
    credentials = tmp_path / 'credentials.env'
    credentials.write_text(
        "export IWENCAI_APIKEYS\nIWENCAI_APIKEYS='[\"key-first\",\"key-last\"]'\n",
        encoding='utf-8',
    )
    monkeypatch.delenv('IWENCAI_APIKEYS', raising=False)
    keys = module.configure_iwencai_rotation(credentials)
    assert keys == ['key-first', 'key-last']
    assert 'IWENCAI_API_KEY' not in module.os.environ


def test_low_chip_build_rejects_missing_iwencai_pool(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.delenv('IWENCAI_APIKEYS', raising=False)
    with pytest.raises(RuntimeError, match='IWENCAI_APIKEYS'):
        module.configure_iwencai_rotation(tmp_path / 'missing.env')


def test_main_restores_backup_after_precommit_failure(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'TRADE_DATE_OVERRIDE', '2026-09-18')
    restored = []
    state = {'sentinel': True}

    def fake_run(args, timeout=900, env=None, **kwargs):
        if args[:3] == ['git', 'rev-parse', 'HEAD'] or args[:3] == ['git', 'rev-parse', 'origin/main']:
            return subprocess.CompletedProcess(args, 0, 'same\n', '')
        if args[:3] == ['git', 'status', '--porcelain']:
            return subprocess.CompletedProcess(args, 0, '', '')
        if 'scripts/build_low_chip_base.py' in args:
            raise RuntimeError('forced precommit failure')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setattr(module, 'publish_lock', nullcontext)
    monkeypatch.setattr(module, 'backup_generated_state', lambda _path: state)
    monkeypatch.setattr(module, 'restore_generated_state', lambda path, got: restored.append((path, got)))

    assert module.main() == 1
    assert len(restored) == 1
    assert restored[0][1] is state


def test_push_success_flag_is_set_only_after_push_returns():
    source = SCRIPT.read_text(encoding='utf-8')
    push = source.index("run(['git', 'push'")
    pushed = source.index('pushed = True', push)
    release = source.index('release = run([sys.executable, \'-c\', release_code]')
    rollback = source.index("if backup_state is not None and base_head is not None and not pushed")
    assert push < pushed < release < rollback
    assert "rollback_candidate(base_head, candidate_head, backup_dir, backup_state)" in source


def test_release_rejects_uncommitted_generators_tests_and_generated_data():
    module = load_module()
    # shadow 脏文件豁免清单已统一为单一来源 scripts/shadow_dirty_files.py（2026-08-24 重构）。
    # 断言跟随该来源，避免新增 shadow 数据源时测试与实现漂移。
    from scripts.shadow_dirty_files import SHADOW_DIRTY_FILES

    assert module.ALLOWED_DIRTY == set(SHADOW_DIRTY_FILES)
    assert 'public/data/korea-tech-factor-shadow.json' in module.ALLOWED_DIRTY
    assert 'public/data/us-selector-shadow.json' in module.ALLOWED_DIRTY
    source = SCRIPT.read_text(encoding='utf-8')
    assert 'foreign -= generated' not in source
    assert 'if staged()' in source


def test_wrapper_holds_shared_publish_lock_for_entire_main():
    source = SCRIPT.read_text(encoding='utf-8')
    assert "PUBLISH_LOCK = Path('/root/.hermes/state/etf-site-publish.lock')" in source
    assert 'def publish_lock()' in source
    assert 'with publish_lock():' in source
    assert 'return run_pipeline()' in source


def test_commit_gate_revalidates_head_dirty_and_exact_staged_set():
    source = SCRIPT.read_text(encoding='utf-8')
    assert "if run(['git', 'rev-parse', 'HEAD']" in source
    assert "precommit_changed = dirty() & allowed" in source
    assert "if staged() != changed" in source
    assert "staged paths differ from release scope" in source


def test_build_quarantines_external_dirty_files_so_catalog_matches():
    """2026-10-22: a concurrent job rewrote 3 ALLOWED_DIRTY shadow files while the
    low-chip chain ran. `npm run build` regenerated catalog.json from those dirty
    bytes, the contract validator disagreed, and build exited 2 — rolling back a
    fully successful 291-stock pipeline. Reset-to-HEAD before build fixes it."""
    module = load_module()
    assert hasattr(module, 'quarantine_external_dirty')
    assert hasattr(module, 'restore_quarantined')
    source = SCRIPT.read_text(encoding='utf-8')
    quarantine = source.index('quarantined = quarantine_external_dirty()')
    build = source.index("run(['npm', 'run', 'build'], 900)")
    restore = source.index('restore_quarantined(quarantined)')
    assert quarantine < build < restore


def test_quarantine_only_touches_allowed_dirty_and_restores_bytes(tmp_path, monkeypatch):
    """Release-scope files must never be quarantined, and the saved bytes must
    round-trip exactly — the owning job's snapshot cannot be lost."""
    module = load_module()
    allowed = tmp_path / 'public/data/shadow.json'
    foreign = tmp_path / 'public/data/a-low-chip-stocks.json'
    for path, body in ((allowed, b'{"dirty": true}'), (foreign, b'{"release": true}')):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    monkeypatch.setattr(module, 'ALLOWED_DIRTY', {'public/data/shadow.json'})
    monkeypatch.setattr(module, 'dirty', lambda: {'public/data/shadow.json', 'public/data/a-low-chip-stocks.json'})
    monkeypatch.setattr(
        module.subprocess, 'run',
        lambda *a, **k: type('R', (), {'stdout': b'{"head": true}'})(),
    )

    saved = module.quarantine_external_dirty()
    assert set(saved) == {'public/data/shadow.json'}, 'release-scope file must not be quarantined'
    assert allowed.read_bytes() == b'{"head": true}'
    assert foreign.read_bytes() == b'{"release": true}'

    module.restore_quarantined(saved)
    assert allowed.read_bytes() == b'{"dirty": true}'


def test_quarantine_isolates_all_foreign_build_inputs(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    originals = {
        'scripts/publish_a_share_nightly.py': b'foreign source',
        'tests/test_a_share_nightly_pipeline.py': b'foreign test',
        'src/content/blog/2026-10-29.md': b'foreign article',
        'src/content/blog/2026-10-30.md': b'untracked article',
        'public/data/new-shadow.json': b'foreign shadow',
        'public/data/deleted-shadow.json': None,
        module.DATA_RELATIVE: b'owned candidate',
    }
    head = {rel: b'HEAD:' + rel.encode() for rel in originals
            if rel != 'src/content/blog/2026-10-30.md'}
    for rel, body in originals.items():
        if body is not None:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    monkeypatch.setattr(module, 'dirty', lambda: set(originals))

    def git(args, **kwargs):
        rel = args[-1].removeprefix('HEAD:')
        if args[1] == 'show':
            if rel not in head:
                raise subprocess.CalledProcessError(128, args)
            return subprocess.CompletedProcess(args, 0, head[rel], b'')
        assert args[1] == 'ls-tree'
        return subprocess.CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(module.subprocess, 'run', git)
    saved = module.quarantine_external_dirty()
    assert set(saved) == set(originals) - {module.DATA_RELATIVE}
    for rel in saved:
        path = tmp_path / rel
        if rel in head:
            assert path.read_bytes() == head[rel]
        else:
            assert not path.exists()
    assert (tmp_path / module.DATA_RELATIVE).read_bytes() == b'owned candidate'
    module.restore_quarantined(saved)
    for rel, body in originals.items():
        path = tmp_path / rel
        assert path.read_bytes() == body if body is not None else not path.exists()


def test_quarantine_restores_prior_files_when_git_lookup_fails(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    originals = {'scripts/a.py': b'a dirty', 'scripts/b.py': b'b dirty'}
    for rel, body in originals.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    monkeypatch.setattr(module, 'dirty', lambda: set(originals))

    def git(args, **kwargs):
        if args[-1].endswith('a.py'):
            return subprocess.CompletedProcess(args, 0, b'a HEAD', b'')
        raise subprocess.TimeoutExpired(args, 60)

    monkeypatch.setattr(module.subprocess, 'run', git)
    with pytest.raises(subprocess.TimeoutExpired):
        module.quarantine_external_dirty()
    for rel, body in originals.items():
        assert (tmp_path / rel).read_bytes() == body


@pytest.fixture
def mocked_release(tmp_path, monkeypatch):
    """Exercise the orchestrator with every command/network boundary mocked."""
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'Path', lambda value: tmp_path / 'backup' if str(value) == '/tmp/low-chip-cron-backup' else Path(value))
    monkeypatch.delenv('LOW_CHIP_SKIP_BUILD', raising=False)
    for name, rel in {
        'DATA': module.DATA_RELATIVE,
        'TRACKING': 'public/data/low-chip-tracking.json',
        'FUYAO_SHADOW': 'public/data/model-lab/low-chip-fuyao-shadow.json',
        'FTSHARE_INDUSTRY_CACHE': 'public/data/model-lab/ftshare-sw-industry-map.json',
        'INDEX': 'public/data/low-chip-history-index.json',
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'data_as_of': '2026-09-18'}))
        monkeypatch.setattr(module, name, path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    monkeypatch.setattr(module, 'PREFETCH_MARKER', tmp_path / 'absent-marker')
    monkeypatch.setattr(module, 'TRADE_DATE_OVERRIDE', '2026-09-18')
    monkeypatch.setattr(module, 'recover_pending_release', lambda: None)
    monkeypatch.setattr(module, 'release_calendar_gate', lambda _day: {'status': 'run'})
    monkeypatch.setattr(module, 'configure_iwencai_rotation', lambda: None)
    monkeypatch.setattr(module, 'load_env_file', lambda *_args: {})
    monkeypatch.setattr(module, 'run_enrichments', lambda: None)
    monkeypatch.setattr(module, 'backup_generated_state', lambda _path: {})
    monkeypatch.setattr(module, 'rollback_candidate', lambda *_args: None)
    monkeypatch.setattr(module, 'write_release_state', lambda *_args: None)
    def sync(*_args):
        state['d1_started'] = True
        return 'token'
    monkeypatch.setattr(module, 'sync_d1_metrics', sync)
    monkeypatch.setattr(module, 'verify_d1_api', lambda *_args: {'count': 1})
    monkeypatch.setattr(module, 'verify_history_index', lambda *_args: {'dates': 1, 'rows': 1})
    monkeypatch.setattr(module, 'validate_payload', lambda _payload: {
        'trade_date': '2026-09-18', 'final': 1, 'intersection_before': 1,
        'excluded_bj': 0, 'excluded_unlock': 0, 'quality': 0, 'institutional': 0,
    })
    monkeypatch.setattr(module, 'run_risk_staging_gate', lambda: {'coverage': {'attached': 1, 'requested': 1}})
    monkeypatch.setattr(module, 'tree_digest', lambda _path: 'verified-dist')
    state = {'generated': False, 'committed': False, 'pushed': False, 'staged': set(), 'calls': [],
             'index_blobs': {}, 'candidate_blobs': {}, 'real_run': module.run,
             'real_subprocess': subprocess.run, 'real_dirty': module.dirty, 'real_staged': module.staged}
    foreign = {'scripts/publish_a_share_nightly.py', 'tests/test_a_share_nightly_pipeline.py',
               'src/content/blog/2026-10-29.md', 'src/content/blog/2026-10-30.md',
               'public/data/new-shadow.json'}
    state['foreign'] = foreign
    monkeypatch.setattr(module, 'dirty', lambda: foreign | (
        {module.DATA_RELATIVE} if state['generated'] and not state['committed'] else set()))
    monkeypatch.setattr(module, 'staged', lambda: state['staged'])
    monkeypatch.setattr(module, 'quarantine_external_dirty', lambda: {'fixture': b'saved'})
    monkeypatch.setattr(module, 'restore_quarantined', lambda saved: state['calls'].append(['restore', saved]))

    def runner(args, timeout=900, env=None, **kwargs):
        state['calls'].append(args)
        output = ''
        if args == ['git', 'rev-parse', 'HEAD']:
            output = 'candidate' if state['committed'] else 'base'
        elif args == ['git', 'rev-parse', 'origin/main']:
            output = 'candidate' if state['pushed'] else 'base'
        elif args[:4] == ['git', 'show', '-s', '--format=%P']:
            output = 'base'
        elif args[:2] == ['git', 'diff-tree']:
            output = module.DATA_RELATIVE + '\0'
        elif 'scripts/build_low_chip_base.py' in args:
            state['generated'] = True
        elif args == ['npm', 'run', 'build']:
            for rel in module.release_paths():
                source = tmp_path / rel
                if source.exists():
                    destination = tmp_path / 'dist' / rel.removeprefix('public/')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read_bytes())
        elif args[:3] == ['git', 'add', '--']:
            state['staged'] = set(args[3:])
            state['index_blobs'] = {rel: (tmp_path / rel).read_bytes() for rel in args[3:]}
        elif args[:2] == ['git', 'commit']:
            state['candidate_blobs'] = dict(state['index_blobs'])
            state['committed'] = True
            state['staged'] = set()
        elif args[:2] == ['git', 'push']:
            state['pushed'] = True
        return subprocess.CompletedProcess(args, 0, output, '')

    monkeypatch.setattr(module, 'run', runner)
    def blob_read(args, **kwargs):
        assert args[:2] == ['git', 'show']
        revision, rel = args[2].split(':', 1)
        blobs = state['index_blobs'] if revision == '' else state['candidate_blobs']
        body = blobs.get(rel, (tmp_path / rel).read_bytes())
        return subprocess.CompletedProcess(args, 0, body, b'')
    monkeypatch.setattr(module.subprocess, 'run', blob_read)
    return module, state


@pytest.mark.parametrize('hook_kind', ['foreign-add', 'foreign-rename', 'extra-parent', 'merge-parent'])
def test_real_git_hook_candidate_scope_blocks_before_d1(mocked_release, monkeypatch, capsys, hook_kind):
    module, state = mocked_release
    root = module.ROOT
    monkeypatch.setattr(module.subprocess, 'run', state['real_subprocess'])
    monkeypatch.setattr(module, 'dirty', state['real_dirty'])
    monkeypatch.setattr(module, 'staged', state['real_staged'])
    real_run = state['real_run']
    real_run(['git', 'init', '-q'])
    real_run(['git', 'config', 'user.name', 'Test'])
    real_run(['git', 'config', 'user.email', 'test@example.invalid'])
    (root / '.gitignore').write_text('/dist/\n')
    (root / 'foreign.txt').write_bytes(b'foreign base')
    real_run(['git', 'add', '.'])
    real_run(['git', 'commit', '-qm', 'base'])
    base = real_run(['git', 'rev-parse', 'HEAD']).stdout.strip()
    real_run(['git', 'update-ref', 'refs/remotes/origin/main', base])
    hook = root / ('.git/hooks/post-commit' if hook_kind in {'extra-parent', 'merge-parent'} else '.git/hooks/pre-commit')
    actions = {
        'foreign-add': "printf 'hook edit' > foreign.txt\ngit add -- foreign.txt\n",
        'foreign-rename': 'git mv -- foreign.txt renamed.txt\n',
        'extra-parent': ('tree=$(git rev-parse HEAD^{tree})\n'
                         'extra=$(printf extra | git commit-tree "$tree" -p HEAD)\n'
                         'git update-ref HEAD "$extra"\n'),
        'merge-parent': ('tree=$(git rev-parse HEAD^{tree})\n'
                         'extra=$(printf extra | git commit-tree "$tree")\n'
                         'merge=$(printf merge | git commit-tree "$tree" -p "' + base + '" -p "$extra")\n'
                         'git update-ref HEAD "$merge"\n'),
    }
    hook.write_text('#!/bin/sh\nset -e\n' + actions[hook_kind])
    hook.chmod(0o755)
    mocked_run = module.run

    def runner(args, timeout=900, env=None, **kwargs):
        if args[0] == 'git':
            if args[1] in {'fetch', 'push'}:
                assert args[1] == 'fetch', 'candidate reached push'
                return subprocess.CompletedProcess(args, 0, '', '')
            result = real_run(args, timeout, env, **kwargs)
            if args[1] == 'commit':
                state['committed'] = True
            return result
        result = mocked_run(args, timeout, env)
        if 'scripts/build_low_chip_base.py' in args:
            module.DATA.write_text('{"data_as_of":"2026-09-18","generated":true}')
        return result

    monkeypatch.setattr(module, 'run', runner)
    assert module.run_pipeline() == 1
    assert state['committed'] is True
    assert state.get('d1_started', False) is False
    assert 'candidate' in capsys.readouterr().out


def test_owned_hashes_bind_real_index_candidate_and_deleted_history(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    rel = module.DATA_RELATIVE
    history = 'public/data/low-chip-history/2026-09-17.json'
    source = tmp_path / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b'base\r\n')
    old = tmp_path / history
    old.parent.mkdir()
    old.write_bytes(b'history')
    subprocess.run(['git', 'add', '--', rel, history], cwd=tmp_path, check=True)
    commit = ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture']
    subprocess.run(commit, cwd=tmp_path, check=True)
    old.unlink()
    source.write_bytes(b'verified\r\n\x00\xff')
    assert history in module.release_paths()
    expected = module.owned_hashes({rel, history})
    with pytest.raises(RuntimeError, match='owned bytes changed'):
        module.verify_owned_hashes(expected, '')
    subprocess.run(['git', 'add', '--', rel, history], cwd=tmp_path, check=True)
    module.verify_owned_hashes(expected, '')
    subprocess.run(commit, cwd=tmp_path, check=True)
    module.verify_owned_hashes(expected, 'HEAD')
    dist = tmp_path / 'dist/data/a-low-chip-stocks.json'
    dist.parent.mkdir(parents=True)
    dist.write_bytes(source.read_bytes())
    module.verify_owned_dist(expected)
    dist.write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='owned dist bytes'):
        module.verify_owned_dist(expected)


def test_release_paths_includes_deleted_tracked_history(tmp_path, monkeypatch):
    module = load_module()
    rel = 'public/data/low-chip-history/2026-09-17.json'
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    monkeypatch.setattr(module, 'dirty', lambda: {rel})
    assert rel in module.release_paths()


def test_run_preserve_newlines_is_opt_in(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    args = [str(Path(module.sys.executable).resolve()), '-c',
            'import os; os.write(1, b"out\\r\\n"); os.write(2, b"err\\r")']
    default = module.run(args)
    assert (default.stdout, default.stderr) == ('out\n', 'err\n')
    preserved = module.run(args, preserve_newlines=True)
    assert (preserved.stdout, preserved.stderr) == ('out\r\n', 'err\r')


@pytest.mark.parametrize('suffix', ['', '\r', '\r\n', '\udcff\r\n'])
def test_dirty_preserves_unicode_whitespace_and_both_rename_paths(tmp_path, monkeypatch, suffix):
    module = load_module()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path / 'public/data/low-chip-history')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    source = 'public/中文 原稿.md' + suffix
    destination = 'public/中文 新稿.md' + suffix
    path = tmp_path / source
    path.parent.mkdir()
    path.write_bytes(b'base')
    subprocess.run(['git', 'add', '--', source], cwd=tmp_path, check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'fixture'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'mv', '--', source, destination], cwd=tmp_path, check=True)
    odd = 'public/ 新增\n尾部 .md '
    (tmp_path / odd).write_bytes(b'new')
    assert module.dirty() == {source, destination, odd}
    assert module.staged() == {destination}
    saved = module.quarantine_external_dirty()
    assert (tmp_path / source).read_bytes() == b'base'
    assert not (tmp_path / destination).exists()
    module.restore_quarantined(saved)
    assert not (tmp_path / source).exists()
    assert (tmp_path / destination).read_bytes() == b'base'
    assert (tmp_path / odd).read_bytes() == b'new'


@pytest.mark.parametrize('rel', [
    'public/data/low-chip-tracking.json',
    'public/data/low-chip-history-index.json',
    'public/data/model-lab/ftshare-sw-industry-map.json',
    'public/data/model-lab/low-chip-fuyao-shadow.json',
    'public/data/low-chip-history/2026-09-17.json',
])
def test_release_blocks_every_preexisting_owned_path(mocked_release, monkeypatch, rel):
    module, state = mocked_release
    original = module.dirty
    monkeypatch.setattr(module, 'dirty', lambda: original() | {rel})
    assert module.run_pipeline() == 1
    assert state['generated'] is False
    assert state['pushed'] is False


@pytest.mark.parametrize('boundary', ['success', 'calendar', 'validation', 'candidate'])
def test_core_only_full_pipeline_keeps_core_and_release_gates(mocked_release, monkeypatch, boundary):
    module, state = mocked_release
    monkeypatch.setenv('LOW_CHIP_CORE_ONLY', '1')
    events = []
    monkeypatch.setattr(module, 'configure_unlock_run', lambda: events.append('unlock-context'))
    monkeypatch.setattr(module, 'run_enrichments', lambda: events.append('enrichment'))
    if boundary == 'calendar':
        monkeypatch.setattr(module, 'release_calendar_gate', lambda day: {'status': 'blocked'})
    original_validate = module.validate_payload
    def validate(payload):
        events.append('validation')
        if boundary == 'validation':
            raise RuntimeError('fixture core validation blocked')
        return original_validate(payload)
    monkeypatch.setattr(module, 'validate_payload', validate)
    original_run = module.run
    def runner(args, timeout=900, env=None, **kwargs):
        result = original_run(args, timeout, env, **kwargs)
        if boundary == 'candidate' and args[:2] == ['git', 'diff-tree']:
            return subprocess.CompletedProcess(args, 0, result.stdout + 'foreign.py\0', '')
        return result
    monkeypatch.setattr(module, 'run', runner)
    result = module.run_pipeline()
    assert result == (0 if boundary == 'success' else 1)
    assert state['pushed'] is (boundary == 'success')
    assert state.get('d1_started', False) is (boundary == 'success')
    if boundary == 'calendar':
        assert state['generated'] is False and events == []
        return
    assert state['generated'] is True
    assert events == ['unlock-context', 'enrichment', 'validation']
    commands = state['calls']
    names = [str(arg) for command in commands for arg in command]
    for script in ['sync_low_chip_raw_pool.py', 'query_low_chip_unlocks.py',
                   'enrich_low_chip_stocks.py', 'query_low_chip_holder_em.py',
                   'query_low_chip_main_force.py', 'attach_low_chip_shareholder_metrics.py',
                   'attach_low_chip_financials.py', 'refresh_low_chip_ftshare_industry.py',
                   'archive_low_chip_snapshot.py']:
        assert 'scripts/' + script in names
    for script in ['attach_low_chip_hlp.py', 'attach_low_chip_touchstone.py',
                   'attach_low_chip_technical.py', 'audit_low_chip_fuyao.py']:
        assert 'scripts/' + script not in names
    if boundary != 'validation':
        assert ['npm', 'run', 'build'] in commands
        assert any(c[:2] == ['git', 'commit'] for c in commands)
    assert state['foreign'] <= module.dirty()


def test_release_foreign_dirty_survives_all_publish_gates(mocked_release):
    module, state = mocked_release
    assert module.run_pipeline() == 0
    assert ['git', 'add', '--', module.DATA_RELATIVE] in state['calls']
    assert state['pushed'] is True
    assert state['foreign'] <= module.dirty()


@pytest.mark.parametrize('phase', ['validation', 'build', 'after-build', 'index', 'candidate', 'dist'])
def test_release_binds_verified_bytes_through_candidate(mocked_release, monkeypatch, phase):
    module, state = mocked_release
    original = module.run
    if phase == 'validation':
        validate = module.validate_payload
        def mutate(payload):
            module.DATA.write_bytes(b'{"changed":true}')
            return validate(payload)
        monkeypatch.setattr(module, 'validate_payload', mutate)

    def runner(args, timeout=900, env=None, **kwargs):
        result = original(args, timeout, env, **kwargs)
        if phase == 'build' and args == ['npm', 'run', 'build']:
            module.DATA.write_bytes(b'{"changed":true}')
        elif phase == 'after-build' and args[1:2] == ['-c'] and 'restore_tracked_public_files' in args[2]:
            module.DATA.write_bytes(b'{"changed":true}')
        elif phase == 'index' and args[:3] == ['git', 'add', '--']:
            state['index_blobs'][module.DATA_RELATIVE] = b'{"changed":true}'
        elif phase == 'candidate' and args[:2] == ['git', 'commit']:
            state['candidate_blobs'][module.DATA_RELATIVE] = b'{"changed":true}'
        elif phase == 'dist' and args == ['npm', 'run', 'build']:
            (module.ROOT / 'dist/data/a-low-chip-stocks.json').write_bytes(b'{"changed":true}')
        return result

    monkeypatch.setattr(module, 'run', runner)
    assert module.run_pipeline() == 1
    assert state['pushed'] is False
    assert state.get('d1_started', False) is False


def test_release_helper_runs_with_foreign_inputs_quarantined(mocked_release, monkeypatch):
    module, state = mocked_release
    active = False
    original_run = module.run

    def quarantine():
        nonlocal active
        active = True
        return {}

    def restore(_saved):
        nonlocal active
        active = False

    def runner(args, timeout=900, env=None, **kwargs):
        if args[:3] == ['npm', 'run', 'build'] or (args[1:2] == ['-c']):
            assert active, f'foreign inputs visible to build/release helper: {args}'
        return original_run(args, timeout, env, **kwargs)

    monkeypatch.setattr(module, 'quarantine_external_dirty', quarantine)
    monkeypatch.setattr(module, 'restore_quarantined', restore)
    monkeypatch.setattr(module, 'run', runner)
    assert module.run_pipeline() == 0
    assert active is False


@pytest.mark.parametrize('phase', ['build', 'release'])
def test_foreign_inputs_restore_after_command_failure(mocked_release, monkeypatch, phase):
    module, state = mocked_release
    original_run = module.run

    def runner(args, timeout=900, env=None, **kwargs):
        if (phase == 'build' and args == ['npm', 'run', 'build']) or (
            phase == 'release' and len(args) > 2 and 'print(release_pages' in args[2]
        ):
            raise RuntimeError('forced command failure')
        return original_run(args, timeout, env, **kwargs)

    monkeypatch.setattr(module, 'run', runner)
    assert module.run_pipeline() == 1
    assert state['calls'][-1] == ['restore', {'fixture': b'saved'}]


@pytest.mark.parametrize('stage', ['startup', 'precommit', 'prepush'])
def test_release_still_blocks_foreign_index_entries(mocked_release, monkeypatch, stage):
    module, state = mocked_release
    original_staged = module.staged

    def staged():
        build_seen = ['npm', 'run', 'build'] in state['calls']
        blocked = stage == 'startup' or (stage == 'precommit' and build_seen) or (
            stage == 'prepush' and state['committed'])
        return {'scripts/foreign.py'} if blocked else original_staged()

    monkeypatch.setattr(module, 'staged', staged)
    assert module.run_pipeline() == 1
    assert state['pushed'] is False


def test_release_still_blocks_owned_changes_before_push(mocked_release, monkeypatch):
    module, state = mocked_release
    original_dirty = module.dirty
    monkeypatch.setattr(module, 'dirty', lambda: original_dirty() | (
        {module.DATA_RELATIVE} if state['committed'] else set()))
    assert module.run_pipeline() == 1
    assert state['pushed'] is False


def test_dist_and_candidate_identity_are_rechecked_before_release():
    source = SCRIPT.read_text(encoding='utf-8')
    canonical = source.index('run([sys.executable, \'-c\', canonicalize_dist]')
    digest = source.index("built_dist_digest = tree_digest(ROOT / 'dist')")
    assert canonical < digest
    assert "if tree_digest(ROOT / 'dist') != built_dist_digest" in source
    assert "candidate_head = run(['git', 'rev-parse', 'HEAD']" in source
    assert "if run(['git', 'rev-parse', 'origin/main']" in source
    assert "pending_owned = dirty() & allowed" in source


def test_skip_build_requires_target_date_before_enrichment() -> None:
    source = SCRIPT.read_text(encoding='utf-8')
    # 跳步模式（env 或预抓标记）必须在任何 build 之前校验 DATA 的目标交易日。
    gate = source.index("skip-build mode requires DATA on ")
    gate_block = source.index("if os.environ.get('LOW_CHIP_SKIP_BUILD') == '1' or prefetch_ready")
    build = source.index("run([sys.executable, 'scripts/build_low_chip_base.py'")
    assert gate_block < build
    assert gate > build or "prefetch_ready = PREFETCH_MARKER.exists()" in source
    # 门禁在 run_pipeline 内、enrichment 全部动作之前执行。
    enrich = source.index("run([sys.executable, 'scripts/enrich_low_chip_stocks.py']")
    assert gate_block < enrich


def test_prefetch_marker_is_called_as_function_in_run_pipeline() -> None:
    """prefetch_marker 是函数对象；误传函数（漏括号）会在 marker.get() 上炸 AttributeError。

    2026-10-16 事故：run_pipeline 里 `_prefetch_marker_matches(prefetch_marker, ...)`
    漏了调用括号，16:00 主链在第一个业务步骤（build）之前就崩溃，
    cron 报 `'function' object has no attribute 'get'`。纯源码字符串检查抓不到，
    必须真正调用验证函数对象行为 + 断言 run_pipeline 源码里是带括号的调用形态。
    """
    module = load_module()
    # 契约1：prefetch_marker 可调用并返回 dict（单参/无参调用均可由 marker 形参支持）。
    marker = module.prefetch_marker(Path('/tmp/definitely-missing-low-chip-prefetch.json'))
    assert isinstance(marker, dict)
    # 契约2：函数对象直接喂给 _prefetch_marker_matches 必须炸——锁定回归语义。
    with pytest.raises(AttributeError):
        module._prefetch_marker_matches(module.prefetch_marker, '2026-10-16')
    # 契约3：正确的 dict 调用路径正常返回 bool，不抛。
    verdict = module._prefetch_marker_matches({}, '2026-10-16')
    assert verdict is False  # 空 marker 不匹配任何交易日
    # 契约4：run_pipeline 源码必须调用 prefetch_marker()（带括号），而非传函数对象。
    source = SCRIPT.read_text(encoding='utf-8')
    assert '_prefetch_marker_matches(prefetch_marker(), trade_date)' in source
    assert '_prefetch_marker_matches(prefetch_marker, trade_date)' not in source


def test_large_pool_timeouts_cover_verified_baostock_runtime() -> None:
    source = SCRIPT.read_text(encoding='utf-8')
    # 2026-10-29: the pool jumped from 169 to 442 stocks (broad selloff). The
    # budgets below were raised together with the host script, but this test was
    # left on the old numbers — the mismatch failed the 22:30 A-share nightly
    # publish (the candidate-commit pytest gate), which has nothing to do with
    # the live data. Keep both sides in sync.
    assert "run([hlp_python, 'scripts/attach_low_chip_hlp.py'], 4200)" in source
    assert "run([hlp_python, 'scripts/attach_low_chip_touchstone.py'], 4500)" in source
    # tracking 已拆到独立 cron（16:40），主链路不再跑它；fetch 超时仍覆盖。
    # 2026-10-22: 池子从 52 只扩到 291 只（普跌），enrichment 是 67+67 批次，
    # 2400s 在 47 个 profile 分页处超时，预算先提到 5400s。
    # 2026-10-29: 池子再扩到 442 只（89+89 批次），预算提到 10800s。
    assert "runner([sys.executable, 'scripts/fetch_low_chip_enrichments.py'], 10800)" in source
    assert 'update_low_chip_tracking.py' not in source


def test_enrichment_fan_out_is_bounded_and_scales_with_pool_size() -> None:
    """The enrichment stage must not fan out without bound as the pool grows.

    2026-10-29 root cause: the pool grew 169 -> 442 stocks, which made the
    iWenCai batch stage 178 serial calls (~54 min) and left FTShare's hardcoded
    900s subprocess timeout just below the measured runtime for 442 stocks.
    When that timeout fired, the exception was swallowed into an empty dict and
    all 442 stocks fell into the per-stock iWenCai fallback chain — the
    multiplication that exhausted the 5400s budget.
    """
    script = Path('/root/projects/etf-rotation-blog/scripts/fetch_low_chip_enrichments.py')
    source = script.read_text(encoding='utf-8')
    # Batches run concurrently instead of one at a time.
    assert 'ThreadPoolExecutor' in source
    assert 'max_workers=IWENCAI_WORKERS' in source
    # The FTShare subprocess timeout scales with the number of codes.
    assert 'FTSHARE_SECONDS_PER_CODE' in source
    assert 'timeout=ftshare_timeout' in source
    assert 'timeout=900' not in source
    # A timeout must be a loud failure, never an empty dict that pushes the
    # whole pool into the per-stock fallback chain.
    assert 'except subprocess.TimeoutExpired' in source


def test_main_force_query_covers_the_whole_pool() -> None:
    """main_force must not silently truncate the pool.

    2026-10-29: pageSize=100 returned only the 100 newest rows of a 442-stock
    pool, so 342 stocks lost their 机构参与度/主力控盘 label without any error.
    """
    script = Path('/root/projects/etf-rotation-blog/scripts/query_low_chip_main_force.py')
    source = script.read_text(encoding='utf-8')
    # The request must not pin pageSize to 100 (the value that truncated the
    # pool). Assert on the request line itself, not on prose in the comments.
    assert 'pageSize=100&' not in source
    assert 'pageSize={PAGE_SIZE}' in source
    assert 'CODE_CHUNK' in source
    # Coverage is verified, so a partial fetch fails loudly.
    assert 'main force coverage incomplete' in source


def test_hlp_and_touchstone_use_same_interpreter_and_touchstone_follows_hlp():
    source = SCRIPT.read_text(encoding='utf-8')
    repo_script = Path('/root/projects/etf-rotation-blog/scripts/attach_low_chip_touchstone.py')
    assert repo_script.exists()
    assert "hlp_python = '/usr/bin/python3'" in source
    hlp = source.index("run([hlp_python, 'scripts/attach_low_chip_hlp.py']")
    touchstone = source.index("run([hlp_python, 'scripts/attach_low_chip_touchstone.py']")
    assert hlp < touchstone
    assert "tests/test_attach_low_chip_touchstone.py" in source
    assert source.index("run(['npm', 'run', 'build']") > touchstone
    assert 'scripts/attach_low_chip_touchstone.py' not in load_module().ALLOWED_DIRTY

    source = SCRIPT.read_text(encoding='utf-8')
    assert "'tests/test_low_chip_tracking_retry.py'" in source


def test_verify_d1_api_requests_default_membership(tmp_path, monkeypatch):
    """Formal membership is verified independently of preserved shadow rows."""
    module = load_module()
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path)
    (tmp_path / '2026-10-22.json').write_text(json.dumps({
        'data_as_of': '2026-10-22', 'intersection': [f'{i:06d}.SZ' for i in range(291)],
    }), encoding='utf-8')
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                'ok': True, 'trade_date': '20261022', 'count': 291,
                'results': [{'stock_code': f'{i:06d}'} for i in range(291)],
            }).encode()

    def opener(request, timeout=30):
        captured['url'] = request.full_url
        return Response()

    module.verify_d1_api('2026-10-22', 291, 'token', opener=opener)
    assert captured['url'] == f'{module.METRICS_ENDPOINT}?date=2026-10-22'


def test_preopen_runs_refuse_to_stamp_the_previous_session_with_a_new_date():
    """A catch-up run that crosses midnight would label the prior session's
    closes with the new trading day. On 2026-10-23 a 00:52 run published 291
    rows dated 10-23 from 10-22 closes, and D1 accepted them."""
    module = load_module()
    source = SCRIPT.read_text(encoding='utf-8')
    assert 'PREOPEN_HOUR' in source
    assert 'refusing to publish before' in source
    # The guard must sit before the calendar gate so nothing is written first.
    guard = source.index('refusing to publish before')
    gate = source.index('calendar_gate = release_calendar_gate(trade_date)')
    assert guard < gate
    # An explicit override must still allow a historical backfill.
    assert 'if not TRADE_DATE_OVERRIDE and now.hour < PREOPEN_HOUR' in source


def test_rollback_refusal_reports_leftovers_instead_of_vanishing():
    """When another job commits mid-run, refusing to roll back is correct — but
    the run must name the paths it left behind. On 2026-10-22 the rolling-compass
    18:46 commit blocked a rollback and only one line was printed, leaving a
    dirty worktree that blocks the next run."""
    module = load_module()
    source = SCRIPT.read_text(encoding='utf-8')
    assert 'rollback refused: concurrent HEAD' in source
    assert '需人工清理的残留' in source
    # The diagnostic must run inside the rollback-failure handler, not elsewhere.
    handler = source.index('低筹码股回滚失败')
    leftovers = source.index('需人工清理的残留')
    assert handler < leftovers


def test_rollback_refusal_does_not_reset_a_foreign_commit():
    """The guard exists so a low-chip failure can never destroy another job's
    commit; it must stay fail-closed rather than force-resetting HEAD."""
    module = load_module()
    source = SCRIPT.read_text(encoding='utf-8')
    guard = source.index("if current_head not in allowed_heads:")
    reset = source.index("run(['git', 'reset', '--mixed', base_head], 60)")
    assert guard < reset, 'HEAD must be validated before any reset'


def test_missing_contract_field_names_the_owning_stage():
    """A bare 'contract missing' line is what made the 2026-10-22 22:33 failure
    take hours to trace. technical_filters is written only at the END of
    attach_low_chip_technical.py, so its absence means that stage died before
    its final write — the message must say so."""
    module = load_module()
    payload = {
        'data_as_of': '2026-10-22', 'intersection': [],
        'intersection_before_filters': [], 'filters': {}, 'enrichments': {},
    }
    with pytest.raises(RuntimeError) as exc:
        module.validate_payload(payload)
    message = str(exc.value)
    assert 'technical_filters' in message
    assert 'attach_low_chip_technical.py' in message
    assert 'killed mid-run' in message
    assert 'attach_low_chip_financials.py' in message
