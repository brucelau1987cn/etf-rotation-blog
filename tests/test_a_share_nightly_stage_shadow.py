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
