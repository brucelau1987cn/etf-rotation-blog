from __future__ import annotations

import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NIGHTLY_STAGE = ROOT / 'scripts/run_a_share_nightly_stage.py'
NIGHTLY_CHAIN = ROOT / 'scripts/run_a_share_nightly_chain.sh'
HERMES_STAGE_WRAPPER = Path('/root/.hermes/scripts/run_a_share_nightly_stage.py')
HERMES_CHAIN_WRAPPER = Path('/root/.hermes/scripts/run_a_share_nightly_chain.sh')
LOCAL_HERMES_READABLE = (
    os.environ.get('CI', '').lower() != 'true'
    and all(os.access(path, os.R_OK) for path in (HERMES_STAGE_WRAPPER, HERMES_CHAIN_WRAPPER))
)


def load():
    spec = importlib.util.spec_from_file_location('nightly_stage', NIGHTLY_STAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_chain_includes_fundamental_shadow_after_cache(monkeypatch, tmp_path):
    module = load()
    names = module.resolve_stages('precheck-cache')
    assert names == ['precheck', 'cache', 'fundamental-shadow']
    command = module.STAGES['fundamental-shadow']
    assert command[0] == '/usr/bin/python3'
    assert command[-3:] == ['--workers', '4', '--write']
    credentials = tmp_path / 'credentials.env'
    credentials.write_text('LOW_CHIP_SYNC_TOKEN=test-token\n', encoding='utf-8')
    monkeypatch.setattr(module, 'CREDENTIALS', credentials)
    env = module.stage_environment('fundamental-shadow')
    assert env['LOW_CHIP_SYNC_TOKEN']
    assert module.stage_environment('cache') is None


def test_repository_chain_invokes_repository_stage_entry():
    wrapper = NIGHTLY_CHAIN.read_text(encoding='utf-8')
    assert 'scripts/run_a_share_nightly_stage.py --stage precheck-cache' in wrapper


@pytest.mark.skipif(not LOCAL_HERMES_READABLE, reason='requires local Hermes wrappers')
def test_hermes_entries_are_thin_repository_forwarders():
    stage_wrapper = HERMES_STAGE_WRAPPER.read_text(encoding='utf-8')
    chain_wrapper = HERMES_CHAIN_WRAPPER.read_text(encoding='utf-8')
    assert 'scripts/run_a_share_nightly_stage.py' in stage_wrapper
    assert 'scripts/run_a_share_nightly_chain.sh' in chain_wrapper
    assert 'def run_command' not in stage_wrapper


def test_nightly_chain_keeps_outer_timeout_above_inner_budget():
    wrapper = NIGHTLY_CHAIN.read_text(encoding='utf-8')
    assert '--timeout 3900' in wrapper
    assert 'timeout 4500s' in wrapper


def test_stage_timeouts_are_capped_per_stage():
    module = load()
    assert module.effective_timeout('precheck', 3600) == 60
    assert module.effective_timeout('cache', 3600) == 1200
    assert module.effective_timeout('fundamental-shadow', 3600) == 2400
    assert module.effective_timeout('cache', 300) == 300


def test_run_stage_persists_running_stage_before_subprocess(monkeypatch):
    module = load()
    statuses = []

    class Completed:
        returncode = 0
        stdout = ''
        stderr = ''

    monkeypatch.setattr(module, 'write_status', lambda payload: statuses.append(payload))
    monkeypatch.setattr(module, 'write_log', lambda *args: None)
    monkeypatch.setattr(module, 'run_command', lambda *args, **kwargs: Completed())
    payload = module.run_stage('precheck', 3600)

    assert payload['ok'] is True
    assert statuses[0]['status'] == 'running'
    assert statuses[0]['current_stage'] == 'precheck'
    assert statuses[0]['requested_stage'] == 'precheck'
    assert statuses[-1]['ok'] is True


def test_successful_precheck_chain_persists_dated_ready_receipt(monkeypatch):
    module = load()
    receipts = []

    class Completed:
        returncode = 0
        stdout = ''
        stderr = ''

    monkeypatch.setattr(module, 'now_iso', lambda: '2026-09-14T22:04:47+08:00')
    monkeypatch.setattr(module, 'write_status', lambda payload: None)
    monkeypatch.setattr(module, 'write_log', lambda *args: None)
    monkeypatch.setattr(module, 'write_json_atomic', lambda path, payload: receipts.append((path, payload)))
    monkeypatch.setattr(module, 'run_command', lambda *args, **kwargs: Completed())
    monkeypatch.setattr(module, 'stage_environment', lambda stage: None)

    payload = module.run_stage('precheck-cache', 3600)

    assert payload['ok'] is True
    assert len(receipts) == 1
    path, receipt = receipts[0]
    assert path == module.CHAIN_READY_PATH
    assert receipt['trade_date'] == '2026-09-14'
    assert receipt['status'] == 'ready'
    assert [item['stage'] for item in receipt['results']] == [
        'precheck', 'cache', 'fundamental-shadow',
    ]


def test_timeout_report_names_the_stage(monkeypatch):
    module = load()

    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(module, 'write_status', lambda payload: None)
    monkeypatch.setattr(module, 'write_log', lambda *args: None)
    monkeypatch.setattr(module, 'stage_environment', lambda stage: {})
    monkeypatch.setattr(module, 'fundamental_shadow_fallback', lambda: None)
    monkeypatch.setattr(module, 'run_command', timeout)
    payload = module.run_stage('fundamental-shadow', 3600)
    assert payload['ok'] is False
    assert 'STAGING BLOCKER: fundamental-shadow timed out after 2400s' in payload['results'][0]['stderr_tail']


def test_timeout_reaps_the_entire_stage_process_group(monkeypatch, tmp_path):
    module = load()
    leader_pid_path = tmp_path / 'leader.pid'
    child_pid_path = tmp_path / 'child.pid'
    script = (
        'import os, signal, subprocess, sys, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'child = subprocess.Popen([sys.executable, "-c", '
        '"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"], '
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        f'open({str(leader_pid_path)!r}, "w").write(str(os.getpid()))\n'
        f'open({str(child_pid_path)!r}, "w").write(str(child.pid))\n'
        'time.sleep(60)\n'
    )
    monkeypatch.setitem(module.STAGES, 'precheck', [sys.executable, '-c', script])
    monkeypatch.setitem(module.STAGE_TIMEOUTS, 'precheck', 1)
    monkeypatch.setattr(module, 'TERMINATE_GRACE_SECONDS', 0.2, raising=False)
    monkeypatch.setattr(module, 'KILL_GRACE_SECONDS', 1.0, raising=False)
    monkeypatch.setattr(module, 'write_status', lambda payload: None)
    monkeypatch.setattr(module, 'write_log', lambda *args: None)

    leader_pid = child_pid = None
    try:
        payload = module.run_stage('precheck', 1)
        leader_pid = int(leader_pid_path.read_text(encoding='utf-8'))
        child_pid = int(child_pid_path.read_text(encoding='utf-8'))
        deadline = time.monotonic() + 2
        while process_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert payload['results'][0]['returncode'] == 124
        assert not Path(f'/proc/{leader_pid}').exists()
        assert not process_is_running(child_pid)
    finally:
        if child_pid is not None and process_is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_timeout_reaps_child_when_leader_exits_on_term(monkeypatch, tmp_path):
    module = load()
    child_pid_path = tmp_path / 'child.pid'
    script = (
        'import subprocess, sys, time\n'
        'child = subprocess.Popen([sys.executable, "-c", '
        '"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"], '
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        f'open({str(child_pid_path)!r}, "w").write(str(child.pid))\n'
        'time.sleep(60)\n'
    )
    monkeypatch.setattr(module, 'TERMINATE_GRACE_SECONDS', 0.2)
    monkeypatch.setattr(module, 'KILL_GRACE_SECONDS', 1.0)

    child_pid = None
    try:
        with pytest.raises(module.subprocess.TimeoutExpired):
            module.run_command(
                [sys.executable, '-c', script], cwd=str(tmp_path), text=True,
                capture_output=True, env=None, timeout=1,
            )
        child_pid = int(child_pid_path.read_text(encoding='utf-8'))
        deadline = time.monotonic() + 2
        while process_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not process_is_running(child_pid)
    finally:
        if child_pid is not None and process_is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def process_is_running(pid: int) -> bool:
    stat_path = Path(f'/proc/{pid}/stat')
    if not stat_path.exists():
        return False
    return stat_path.read_text(encoding='utf-8').split()[2] != 'Z'


def test_cache_validator_does_not_require_iwencai_source():
    validator = Path('/root/.hermes/scripts/update_a_share_cache_nightly.py').read_text(encoding='utf-8')
    importer = Path('/root/projects/etf-rotation-blog/scripts/update_a_share_bar_cache.py').read_text(encoding='utf-8')
    assert "source='iwencai'" not in validator
    assert 'source_counts' in importer


def test_nightly_report_formats_success_as_readable_markdown():
    module = load()
    payload = {
        'ok': True,
        'finished_at': '2026-08-18T20:54:03+08:00',
        'results': [
            {'stage': 'precheck', 'started_at': '2026-08-18T20:50:51+08:00',
             'finished_at': '2026-08-18T20:50:54+08:00', 'ok': True},
            {'stage': 'cache', 'started_at': '2026-08-18T20:50:54+08:00',
             'finished_at': '2026-08-18T20:52:06+08:00', 'ok': True},
            {'stage': 'fundamental-shadow', 'started_at': '2026-08-18T20:52:06+08:00',
             'finished_at': '2026-08-18T20:54:03+08:00', 'ok': True,
             'stdout_tail': 'login success!\n' +
                 '{"trade_date":"2026-08-18","coverage":{"expected":80,"succeeded":80,"empty":0,"failed":0,"coverage":1.0},"observation_sessions":4,"d1_inserted":80}\n'},
        ],
    }
    report = module.format_report(payload)
    assert '## 🌙 A股夜间流水线' in report
    assert '✅ **环境预检** · 3秒' in report
    assert '✅ **行情缓存** · 1分12秒' in report
    assert '**基本面覆盖：** 80/80（100.0%）' in report
    assert '**D1写入：** 80 条' in report
    assert 'login success' not in report
    assert report.strip().endswith('可进入22:00内容生成阶段。')


def test_nightly_report_degrades_safely_and_states_failed_gate():
    module = load()
    payload = {
        'ok': False,
        'finished_at': '2026-08-18T20:54:03+08:00',
        'results': [
            None,
            {'stage': 'fundamental-shadow', 'started_at': None,
             'finished_at': 'invalid', 'ok': False,
             'stderr_tail': 'STAGING BLOCKER fundamental coverage 79/80'},
        ],
    }
    report = module.format_report(payload)
    assert '❌ **基本面影子** · 耗时未知' in report
    assert 'STAGING BLOCKER fundamental coverage 79/80' in report
    assert report.strip().endswith('流水线已阻断，后续阶段停止执行。')


def test_busy_path_writes_status_and_durable_json_log():
    module = load()
    source = NIGHTLY_STAGE.read_text(encoding='utf-8')
    busy_block = source[source.index('except RuntimeError as exc:'):source.index('return 75')]
    assert 'write_status(payload)' in busy_block
    assert 'write_log(payload, args.stage)' in busy_block
    assert callable(module.write_log)


def test_failed_report_with_empty_results_still_states_gate_conclusion():
    module = load()
    report = module.format_report({
        'ok': False,
        'finished_at': '2026-08-18T21:22:00+08:00',
        'results': [None, 'bad'],
    })
    assert '未返回有效阶段结果' in report
    assert report.strip().endswith('流水线已阻断，后续阶段停止执行。')


def test_malformed_coverage_shape_does_not_crash_report():
    module = load()
    payload = {
        'ok': True,
        'finished_at': '2026-08-18T21:22:00+08:00',
        'results': [{
            'stage': 'fundamental-shadow',
            'started_at': '2026-08-18T21:21:00+08:00',
            'finished_at': '2026-08-18T21:22:00+08:00',
            'ok': True,
            'stdout_tail': '{"trade_date":"2026-08-18","coverage":"bad","d1_inserted":80}',
        }],
    }
    report = module.format_report(payload)
    assert '**基本面覆盖：** —/—（—）' in report
    assert report.strip().endswith('可进入22:00内容生成阶段。')


def test_prepare_gate_enforces_pattern_research_isolation_contract():
    source = Path('/root/projects/etf-rotation-blog/scripts/prepare_a_share_nightly.py').read_text(encoding='utf-8')
    assert 'pattern_research must remain research-only' in source
    assert 'pattern_research coverage below 82' in source
