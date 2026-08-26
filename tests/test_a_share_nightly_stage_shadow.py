from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


NIGHTLY_STAGE = Path('/root/.hermes/scripts/run_a_share_nightly_stage.py')
NIGHTLY_CHAIN = Path('/root/.hermes/scripts/run_a_share_nightly_chain.sh')
LOCAL_HERMES_READABLE = (
    os.environ.get('CI', '').lower() != 'true'
    and all(os.access(path, os.R_OK) for path in (NIGHTLY_STAGE, NIGHTLY_CHAIN))
)
pytestmark = pytest.mark.skipif(
    not LOCAL_HERMES_READABLE,
    reason='requires readable local Hermes nightly scripts',
)


def load():
    spec = importlib.util.spec_from_file_location('nightly_stage', NIGHTLY_STAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_chain_includes_fundamental_shadow_after_cache():
    module = load()
    names = module.resolve_stages('precheck-cache')
    assert names == ['precheck', 'cache', 'fundamental-shadow']
    command = module.STAGES['fundamental-shadow']
    assert command[0] == '/usr/bin/python3'
    assert command[-3:] == ['--workers', '4', '--write']
    env = module.stage_environment('fundamental-shadow')
    assert env['LOW_CHIP_SYNC_TOKEN']
    assert module.stage_environment('cache') is None


def test_enabled_nightly_chain_wrapper_invokes_precheck_cache():
    wrapper = NIGHTLY_CHAIN.read_text(encoding='utf-8')
    assert 'run_a_share_nightly_stage.py --stage precheck-cache' in wrapper


def test_stage_timeouts_are_capped_per_stage():
    module = load()
    assert module.effective_timeout('precheck', 3600) == 60
    assert module.effective_timeout('cache', 3600) == 1200
    assert module.effective_timeout('fundamental-shadow', 3600) == 600
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
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: Completed())
    payload = module.run_stage('precheck', 3600)

    assert payload['ok'] is True
    assert statuses[0]['status'] == 'running'
    assert statuses[0]['current_stage'] == 'precheck'
    assert statuses[0]['requested_stage'] == 'precheck'
    assert statuses[-1]['ok'] is True


def test_timeout_report_names_the_stage(monkeypatch):
    module = load()

    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(module, 'write_status', lambda payload: None)
    monkeypatch.setattr(module, 'write_log', lambda *args: None)
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    payload = module.run_stage('fundamental-shadow', 3600)
    assert payload['ok'] is False
    assert 'STAGING BLOCKER: fundamental-shadow timed out after 600s' in payload['results'][0]['stderr_tail']


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
