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


def test_full_build_and_local_commit_precede_remote_side_effects():
    source = SCRIPT.read_text(encoding='utf-8')
    build = source.index("run(['npm', 'run', 'build']")
    commit = source.index("run(['git', 'commit'")
    sync = source.index("token = sync_d1_metrics(summary['trade_date'], summary['final'])")
    verify = source.index("d1 = verify_d1_api(summary['trade_date'], summary['final'], token)")
    history = source.index('history = verify_history_index(token)')
    push = source.index("run(['git', 'push'")
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
    assert "'fuyao_shadow': fuyao_shadow.get('status')" in source
    assert "'public/data/model-lab/low-chip-fuyao-shadow.json'" in source
    assert shadow < source.index("run(['npm', 'run', 'build']")


def test_backup_restore_recovers_exact_pre_run_generated_state(tmp_path, monkeypatch):
    module = load_module()
    data_dir = tmp_path / 'data'
    history_dir = data_dir / 'history'
    history_dir.mkdir(parents=True)
    paths = {
        'DATA': data_dir / 'stocks.json',
        'TRACKING': data_dir / 'tracking.json',
        'FUYAO_SHADOW': data_dir / 'fuyao.json',
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


def test_low_chip_build_uses_last_iwencai_key_from_pool(monkeypatch):
    module = load_module()
    captured = []

    def fake_run(args, timeout=900, env=None):
        captured.append((args, env))
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(module, 'run', fake_run)
    monkeypatch.setenv('IWENCAI_APIKEYS', json.dumps(['key-first', 'key-middle', 'key-last']))
    env = module.iwencai_last_key_env()
    module.run([module.sys.executable, 'scripts/build_low_chip_base.py', '2026-08-25'], 900, env=env)

    assert captured[0][1]['IWENCAI_API_KEY'] == 'key-last'
    assert captured[0][1]['IWENCAI_APIKEYS'] == json.dumps(['key-first', 'key-middle', 'key-last'])


def test_low_chip_build_loads_last_key_from_credentials_file(tmp_path, monkeypatch):
    module = load_module()
    credentials = tmp_path / 'credentials.env'
    credentials.write_text(
        "export IWENCAI_APIKEYS\nIWENCAI_APIKEYS='[\"key-first\",\"key-last\"]'\n",
        encoding='utf-8',
    )
    monkeypatch.delenv('IWENCAI_APIKEYS', raising=False)
    env = module.iwencai_last_key_env(credentials)
    assert env['IWENCAI_API_KEY'] == 'key-last'


def test_low_chip_build_rejects_missing_iwencai_pool(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.delenv('IWENCAI_APIKEYS', raising=False)
    with pytest.raises(RuntimeError, match='IWENCAI_APIKEYS'):
        module.iwencai_last_key_env(tmp_path / 'missing.env')


def test_main_restores_backup_after_precommit_failure(monkeypatch):
    module = load_module()
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
    assert "PUBLISH_LOCK = Path('/root/.hermes/state/etf-paper-publish.lock')" in source
    assert 'def publish_lock()' in source
    assert 'with publish_lock():' in source
    assert 'return run_pipeline()' in source


def test_commit_gate_revalidates_head_dirty_and_exact_staged_set():
    source = SCRIPT.read_text(encoding='utf-8')
    assert "if run(['git', 'rev-parse', 'HEAD']" in source
    assert "precommit_changed = dirty() - ALLOWED_DIRTY" in source
    assert "if staged() != changed" in source
    assert "staged paths differ from release scope" in source


def test_dist_and_candidate_identity_are_rechecked_before_release():
    source = SCRIPT.read_text(encoding='utf-8')
    canonical = source.index('run([sys.executable, \'-c\', canonicalize_dist]')
    digest = source.index("built_dist_digest = tree_digest(ROOT / 'dist')")
    assert canonical < digest
    assert "if tree_digest(ROOT / 'dist') != built_dist_digest" in source
    assert "candidate_head = run(['git', 'rev-parse', 'HEAD']" in source
    assert "if run(['git', 'rev-parse', 'origin/main']" in source
    assert "if dirty() - ALLOWED_DIRTY" in source


def test_release_gate_runs_tracking_retry_regressions():
    source = SCRIPT.read_text(encoding='utf-8')
    assert "'tests/test_low_chip_tracking_retry.py'" in source
