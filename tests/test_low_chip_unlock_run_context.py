"""Offline test for the host publisher's per-run unlock environment."""
import re
from pathlib import Path
import pytest
from test_low_chip_release_cron import LOCAL_HERMES_READABLE, load_module
pytestmark = pytest.mark.skipif(not LOCAL_HERMES_READABLE, reason='host integration')

def test_new_unlock_run_uses_unique_bound_environment(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setenv('LOW_CHIP_RUN_ID', 'stale')
    monkeypatch.setenv('LOW_CHIP_UNLOCK_CACHE', '/stale/cache.json')
    first = module.configure_unlock_run(tmp_path)
    second = module.configure_unlock_run(tmp_path)
    assert first != second
    assert re.fullmatch('[0-9a-f]{32}', second)
    assert module.os.environ['LOW_CHIP_RUN_ID'] == second
    assert module.os.environ['LOW_CHIP_UNLOCK_CACHE'] == str(tmp_path / second / 'unlock.json')
    assert not (tmp_path / second).exists()

def test_run_context_configured_before_query_and_consumer():
    module = load_module()
    source = Path(module.__file__).read_text()
    call = source.index('        configure_unlock_run()')
    assert call < source.index("run([sys.executable, 'scripts/query_low_chip_unlocks.py']")
    assert call < source.index("run([sys.executable, 'scripts/enrich_low_chip_stocks.py']")
