from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    path = Path('/root/.hermes/scripts/run_a_share_nightly_stage.py')
    spec = importlib.util.spec_from_file_location('nightly_stage', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_chain_includes_fundamental_shadow_after_cache():
    module = load()
    names = module.resolve_stages('precheck-cache')
    assert names == ['precheck', 'cache', 'fundamental-shadow']
    command = module.STAGES['fundamental-shadow']
    assert command[-3:] == ['--workers', '4', '--write']
    env = module.stage_environment('fundamental-shadow')
    assert env['LOW_CHIP_SYNC_TOKEN']
    assert module.stage_environment('cache') is None


def test_enabled_nightly_chain_wrapper_invokes_precheck_cache():
    wrapper = Path('/root/.hermes/scripts/run_a_share_nightly_chain.sh').read_text(encoding='utf-8')
    assert 'run_a_share_nightly_stage.py --stage precheck-cache' in wrapper
