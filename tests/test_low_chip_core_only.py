import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path('/root/projects/etf-rotation-blog')

def load(path):
    spec = importlib.util.spec_from_file_location('core_test_' + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket, subprocess
    def blocked(*a, **k):
        raise AssertionError('external IO blocked')
    for obj, name in [(socket, 'create_connection'), (socket, 'getaddrinfo'), (socket.socket, 'connect'), (subprocess, 'run'), (subprocess, 'Popen')]:
        monkeypatch.setattr(obj, name, blocked)

def test_core_degrades_stale_fields_and_preserves_core(tmp_path):
    mod = load(Path('/root/.hermes/scripts/update_low_chip_and_release.py'))
    row = {'financials': {'report_period': '20260930'}, 'shareholder_metrics': {'count': 4}, 'hlp_metrics': {'HLP': 0.1, 'signals': ['低进']}, 'touchstone_metrics': {'touchstone_hit': True}, 'technical': {'rsi': 1}, 'concepts': ['old'], 'theme_concepts': ['old'], 'theme_concept': 'old', 'theme_concept_pool': ['old'], 'sector_with_theme': 'old', 'sector': '半导体', 'etf_holdings': [{'code': 'old'}], 'etf_top_category': 'ETF'}
    payload = {'data_as_of': '2026-10-30', 'intersection': ['000001.SZ'], 'enrichments': {'000001.SZ': row, 'old': dict(row)}}
    mod.DATA = tmp_path / 'data.json'
    mod.FUYAO_SHADOW = tmp_path / 'shadow.json'
    foreign = tmp_path / 'foreign.json'
    foreign.write_text('foreign')
    mod.DATA.write_text(json.dumps(payload))
    mod.degrade_optional_research()
    result = json.loads(mod.DATA.read_text())
    for row in result['enrichments'].values():
        assert row['financials'] == {'report_period': '20260930'}
        assert row['shareholder_metrics'] == {'count': 4}
        assert row['hlp_metrics']['status'] == 'UNAVAILABLE'
        assert not row['hlp_metrics'].get('signals')
        assert not row['touchstone_metrics'].get('touchstone_hit')
        assert 'rsi' not in row['technical']
        assert row['theme_concepts'] == []
        assert row['etf_holdings'] == []
    for key in ['hlp_contract', 'touchstone_contract', 'technical_filters', 'theme_concepts_contract', 'etf_holdings_contract']:
        contract = result[key]
        assert contract['status'] == 'UNAVAILABLE' and contract['degraded'] is True
        assert contract['data_as_of'] == '2026-10-30'
        assert contract['coverage'] == {'requested': 0, 'computed': 0, 'failed': 1}
        assert contract['failed_codes'] == ['000001.SZ']
        assert contract['reason']
    assert set(['rsi_max', 'outer_inner_ratio_min', 'change_20d_min', 'labels', 'missing_codes']) <= result['technical_filters'].keys()
    shadow = json.loads(mod.FUYAO_SHADOW.read_text())
    assert shadow['schema_version'] == 'low-chip-fuyao-shadow-v1'
    assert shadow['mode'] == 'shadow' and shadow['production_effect'] == 'none'
    assert shadow['rows'] == {} and shadow['status'] == 'UNAVAILABLE'
    assert foreign.read_text() == 'foreign'


def test_core_initializes_missing_enrichment(tmp_path):
    mod = load(Path('/root/.hermes/scripts/update_low_chip_and_release.py'))
    mod.DATA = tmp_path / 'data.json'
    mod.FUYAO_SHADOW = tmp_path / 'shadow.json'
    mod.DATA.write_text(json.dumps({'data_as_of': '2026-10-30', 'intersection': ['000001.SZ']}))
    mod.degrade_optional_research()
    result = json.loads(mod.DATA.read_text())
    assert result['enrichments']['000001.SZ']['technical']['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('flag', ['1', '0', 'true', ''])
def test_mode_dispatch_executes_original_pipeline_stages(tmp_path, monkeypatch, flag):
    import ast
    host = Path('/root/.hermes/scripts/update_low_chip_and_release.py')
    mod = load(host)
    monkeypatch.setenv('LOW_CHIP_CORE_ONLY', flag)
    mod.DATA = tmp_path / 'data.json'
    mod.FUYAO_SHADOW = tmp_path / 'shadow.json'
    mod.DATA.write_text(json.dumps({'data_as_of': '2026-10-30', 'intersection': []}))
    mod.FUYAO_SHADOW.write_text('{}')
    calls = []
    mod.run = lambda args, *a, **k: calls.append(args)
    tree = ast.parse(host.read_text())
    # Execute the actual stage block with local data and blocked subprocess IO.
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_pipeline')
    stages = next(n for n in pipeline.body if isinstance(n, ast.Try)).body
    start = next(i for i, n in enumerate(stages) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'core_only' for t in n.targets))
    end = next(i for i, n in enumerate(stages) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'validated_hashes' for t in n.targets))
    exec(compile(ast.Module(body=stages[start:end], type_ignores=[]), str(host), 'exec'), mod.__dict__)
    names = [Path(str(call[1])).name for call in calls]
    optional = ['attach_low_chip_hlp.py', 'attach_low_chip_touchstone.py', 'attach_low_chip_technical.py', 'attach_low_chip_theme_concepts.py', 'attach_low_chip_etf_holdings.py', 'audit_low_chip_fuyao.py']
    assert all((name in names) == (flag != '1') for name in optional)
    assert names.count('archive_low_chip_snapshot.py') == 2
    assert 'refresh_low_chip_ftshare_industry.py' in names
    etf = next(call for call in calls if 'attach_low_chip_industry_etfs.py' in str(call))
    assert ('--skip-profit-query' in etf) == (flag == '1')


def test_skip_profit_preserves_local_mapping(tmp_path, monkeypatch):
    mod = load(ROOT / 'scripts/attach_low_chip_industry_etfs.py')
    data = tmp_path / 'data.json'
    pool = tmp_path / 'pool.json'
    data.write_text(json.dumps({'data_as_of': '2026-10-30', 'intersection': ['000001.SZ'], 'enrichments': {'000001.SZ': {'industry_standard': 'SW2021', 'industry_level2': {'name': '半导体'}, 'industry_etfs': [{'profit_ratio': 99}]}}}))
    rows = [{'code': '512480', 'theme': '半导体', 'tier': 'formal'}] + [{'code': f'8{i:05d}', 'tier': 'formal'} for i in range(90)]
    pool.write_text(json.dumps({'summary': {'universe_count': 91}, 'all_rows': rows}))
    monkeypatch.setattr(mod, 'fetch_etf_profit_ratios', lambda *a: pytest.fail('profit query called'))
    mod.attach_industry_etfs(data, pool, skip_profit_query=True)
    result = json.loads(data.read_text())
    row = result['enrichments']['000001.SZ']
    assert row['industry_etf_status'] == 'matched'
    assert row['industry_etfs'][0]['code'] == '512480.SH'
    assert row['industry_etfs'][0]['profit_ratio'] is None
    assert result['industry_etf_profit_contract']['status'] == 'UNAVAILABLE'
    assert result['industry_etf_profit_contract']['requested'] == 0
