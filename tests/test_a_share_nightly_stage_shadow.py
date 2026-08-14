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
