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

    def runner(args, timeout=900, env=None):
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0, 'D1 sync: 10/10 inserted (2026-08-14)\n', '')

    token = module.sync_d1_metrics('2026-08-14', 10, runner=runner, env_file=env_file)
    assert token == 'test-token'
    assert calls[0][0][-2:] == ['--day', '2026-08-14']
    assert calls[0][1]['LOW_CHIP_SYNC_TOKEN'] == 'test-token'


def test_verify_d1_api_uses_bearer_and_requires_expected_rows():
    module = load_module()
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

    def fake_run(args, timeout=900, env=None):
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
        'results': [{'stock_code': '000001'}, {'stock_code': '2'}],
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

    def fake_run(args, timeout=900, env=None):
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

    def fake_run(args, timeout=900, env=None):
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

    def fake_run(args, timeout=900, env=None):
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

    def runner(args, timeout=900, env=None):
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
    runner = lambda args, timeout=900, env=None: subprocess.CompletedProcess(args, 0, json.dumps(report) + '\n', '')
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
    def fake_run(args, timeout=900, env=None):
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


def test_rollback_refuses_to_overwrite_concurrent_head(monkeypatch, tmp_path):
    module = load_module()
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, timeout=900, env=None: calls.append(args) or subprocess.CompletedProcess(args, 0, 'concurrent-sha\n', ''))

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

    def fake_run(args, timeout=900, env=None):
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
    assert "precommit_changed = dirty() - ALLOWED_DIRTY" in source
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


def test_dist_and_candidate_identity_are_rechecked_before_release():
    source = SCRIPT.read_text(encoding='utf-8')
    canonical = source.index('run([sys.executable, \'-c\', canonicalize_dist]')
    digest = source.index("built_dist_digest = tree_digest(ROOT / 'dist')")
    assert canonical < digest
    assert "if tree_digest(ROOT / 'dist') != built_dist_digest" in source
    assert "candidate_head = run(['git', 'rev-parse', 'HEAD']" in source
    assert "if run(['git', 'rev-parse', 'origin/main']" in source
    assert "if dirty() - ALLOWED_DIRTY" in source


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
    assert "run([hlp_python, 'scripts/attach_low_chip_hlp.py'], 2250)" in source
    assert "run([hlp_python, 'scripts/attach_low_chip_touchstone.py'], 2400)" in source
    # tracking 已拆到独立 cron（16:40），主链路不再跑它；fetch 超时仍覆盖。
    # 2026-10-22: 池子从 52 只扩到 291 只（普跌），enrichment 是 67+67 批次，
    # 2400s 在 47 个 profile 分页处超时，预算提到 5400s。
    assert "runner([sys.executable, 'scripts/fetch_low_chip_enrichments.py'], 5400)" in source
    assert 'update_low_chip_tracking.py' not in source


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


def test_verify_d1_api_requests_include_all_so_writes_are_counted():
    """The default GET applies the UI membership filter, so a complete 291-row
    write reads back as 93 and looks like a failure (2026-10-22)."""
    module = load_module()
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                'ok': True, 'trade_date': '20261022', 'count': 291,
                'results': [{}] * 291,
            }).encode()

    def opener(request, timeout=30):
        captured['url'] = request.full_url
        return Response()

    module.verify_d1_api('2026-10-22', 291, 'token', opener=opener)
    assert 'include_all=1' in captured['url']


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
