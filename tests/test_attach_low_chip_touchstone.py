from pathlib import Path
import sys
import json
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import attach_low_chip_touchstone as module  # noqa: E402
from attach_low_chip_touchstone import calculate_touchstone  # noqa: E402


def test_reference_vector_confirms_trough_only_on_rise_confirmation_bar():
    closes = [100, 120] + [120] * 10 + [100, 80, 70, 75, 81]
    dates = [f'2026-01-{i:02d}' for i in range(1, len(closes) + 1)]
    result = calculate_touchstone(closes, dates)
    assert result['bottom_alert'] is True
    assert result['bottom_confirmed'] is True
    assert result['confirmation_date'] == '2026-01-17'
    assert result['anchor_date'] == '2026-01-15'
    assert result['anchor_close'] == 70
    assert result['confirmation_close'] == 81
    assert result['rise_pct'] == 15.7143


def test_initial_decision_tracks_independent_extrema_and_rise_wins():
    closes = [100, 150, 80, 120, 130, 110]
    dates = [f'2026-05-{i:02d}' for i in range(1, len(closes) + 1)]
    result = calculate_touchstone(closes, dates)
    assert result['confirmation_date'] == '2026-05-04'
    assert result['anchor_date'] == '2026-05-03'
    assert result['anchor_close'] == 80


def test_exact_15_percent_boundary_confirms():
    result = calculate_touchstone([100, 85, 97.75], ['2026-01-01', '2026-01-02', '2026-01-03'])
    assert result['bottom_confirmed'] is True
    assert result['confirmation_date'] == '2026-01-03'


def test_spacing_suppresses_second_trough_within_ten_bars():
    closes = [100, 80, 92, 75, 86.25]
    dates = [f'2026-02-{i:02d}' for i in range(1, len(closes) + 1)]
    result = calculate_touchstone(closes, dates)
    assert result['bottom_alert'] is False
    assert result['bottom_confirmed'] is False
    assert result['last_confirmation_date'] == '2026-02-05'


def test_spacing_allows_confirmation_when_gap_is_ten():
    closes = [100, 80, 92] + [92] * 9 + [75, 86.25]
    dates = [f'2026-03-{i:02d}' for i in range(1, len(closes) + 1)]
    result = calculate_touchstone(closes, dates)
    assert result['bottom_confirmed'] is True
    assert result['anchor_close'] == 75


def test_candidate_anchor_does_not_count_before_formal_confirmation():
    result = calculate_touchstone([100, 80, 90], ['2026-04-01', '2026-04-02', '2026-04-03'])
    assert result['bottom_confirmed'] is False
    assert result['confirmation_date'] is None


def test_cache_history_is_used_before_network(tmp_path, monkeypatch):
    db = tmp_path / 'bars.db'
    with sqlite3.connect(db) as conn:
        from etf_bar_cache import SCHEMA
        conn.executescript(SCHEMA)
        rows = [('XSHE', '000001', f'2025-01-{i:02d}', float(i), float(i), float(i), float(i), 1, 1, 'qfq', 'tencent', 1, 'now') for i in range(1, 10)]
        conn.executemany('INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: (_ for _ in ()).throw(AssertionError('network used')))
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=db)
    assert len(result) == 9
    assert result[0]['trade_date'] == '2025-01-01'


def test_tencent_history_is_clipped_sorted_deduped_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [
        {'trade_date': '2025-01-11', 'close': 99},
        {'trade_date': '2025-01-09', 'close': 9},
        {'trade_date': '2025-01-08', 'close': float('nan')},
        {'trade_date': '2025-01-07', 'close': -1},
        {'trade_date': '2025-01-09', 'close': 10},
        {'trade_date': '2025-01-06', 'close': '6.5'},
    ])
    monkeypatch.setattr(module, 'fetch_baostock_history', lambda *_: (_ for _ in ()).throw(AssertionError('BaoStock used')))
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [
        {'trade_date': '2025-01-06', 'close': 6.5},
        {'trade_date': '2025-01-09', 'close': 10.0},
    ]


def test_baostock_history_receives_same_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [])
    monkeypatch.setattr(module, 'fetch_baostock_history', lambda *_: [
        {'date': '2025-01-09', 'close': 9},
        {'date': '2025-01-10', 'close': 10},
        {'date': '2025-01-08', 'close': float('inf')},
    ])
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]


def test_tencent_exception_falls_back_to_baostock_and_normalizes(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: (_ for _ in ()).throw(OSError('HTTP 501')))
    monkeypatch.setattr(module, 'fetch_baostock_history', lambda *_: [
        {'date': '2025-01-11', 'close': 11},
        {'date': '2025-01-09', 'close': 9},
        {'date': '2025-01-08', 'close': float('nan')},
    ])
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]


def test_cache_exception_continues_to_network(tmp_path, monkeypatch):
    (tmp_path / 'present.db').touch()
    monkeypatch.setattr(module, 'connect', lambda *_: (_ for _ in ()).throw(OSError('cache unavailable')))
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [{'trade_date': '2025-01-09', 'close': 9}])
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'present.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]


def test_stock_api_is_bounded_last_resort(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [])
    monkeypatch.setattr(module, 'fetch_baostock_history', lambda *_: [])
    monkeypatch.setattr(module, 'fetch_stock_api_history', lambda *_: [{'date': '2025-01-09', 'close': 9}])
    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]


def test_baostock_history_logs_in_queries_and_logs_out_on_success(monkeypatch):
    calls = []
    class Result:
        error_code = '0'
        fields = ['date', 'close', 'tradestatus']
        def __init__(self): self.rows = [['2025-01-09', '9', '1']]
        def next(self):
            if self.rows:
                self.rows.pop(0)
                return True
            return False
        def get_row_data(self): return ['2025-01-09', '9', '1']
    class BS:
        def login(self): calls.append('login'); return type('Login', (), {'error_code': '0'})()
        def query_history_k_data_plus(self, *args, **kwargs): calls.append(('query', args[0])); return Result()
        def logout(self): calls.append('logout')
    monkeypatch.setitem(__import__('sys').modules, 'baostock', BS())
    assert module.fetch_baostock_history({'code': '000001', 'market': 'XSHE'}) == [{'trade_date': '2025-01-09', 'close': 9.0}]
    assert calls == ['login', ('query', 'sz.000001'), 'logout']


def test_baostock_history_logs_out_when_query_fails(monkeypatch):
    calls = []
    class BS:
        def login(self): calls.append('login'); return type('Login', (), {'error_code': '0'})()
        def query_history_k_data_plus(self, *args, **kwargs): calls.append('query'); raise RuntimeError('query failed')
        def logout(self): calls.append('logout')
    monkeypatch.setitem(__import__('sys').modules, 'baostock', BS())
    try: module.fetch_baostock_history({'code': '600001', 'market': 'XSHG'})
    except RuntimeError: pass
    else: raise AssertionError('expected query failure')
    assert calls == ['login', 'query', 'logout']


def test_stock_api_uses_canonical_symbol_and_explicit_tencent_source(monkeypatch):
    seen = []
    class Proc:
        returncode = 0; stderr = ''
        stdout = '[{"date":"2025-01-09","open":1,"high":1.1,"low":0.9,"close":1}]'
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: (seen.append(command) or Proc()))
    rows = module.fetch_stock_api_history({'code': '000157', 'market': 'XSHE'}, 5)
    assert rows[0]['close'] == 1.0
    assert seen[0][seen[0].index('get-klines') + 1] == 'SZ000157'
    assert seen[0][-1] == 'tencent'


def test_incomplete_coverage_fails_closed_and_does_not_write(tmp_path):
    original = {'data_as_of': '2025-01-09', 'intersection': ['000001.SZ', '000002.SZ'], 'enrichments': {
        '000001.SZ': {'touchstone_metrics': {'stale': True}}, '000002.SZ': {'touchstone_metrics': {'stale': True}}
    }}
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps(original), encoding='utf-8')
    try:
        module.build_and_publish(target, history_loader=lambda code, end: ([{'trade_date': '2025-01-01', 'close': 1}] if code == '000001.SZ' else []))
    except RuntimeError as exc:
        assert 'complete' in str(exc)
    else:
        raise AssertionError('expected coverage failure')
    assert json.loads(target.read_text(encoding='utf-8')) == original


def test_complete_coverage_removes_stale_metrics_and_writes_atomically(tmp_path):
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({'data_as_of': '2025-01-02', 'intersection': ['000001.SZ'], 'enrichments': {'000001.SZ': {'touchstone_metrics': {'stale': True}}}}), encoding='utf-8')
    module.build_and_publish(target, history_loader=lambda code, end: ([{'trade_date': '2025-01-01', 'close': 1}, {'trade_date': '2025-01-02', 'close': 1}],))
    result = json.loads(target.read_text(encoding='utf-8'))
    assert result['enrichments']['000001.SZ']['touchstone_metrics']['coverage_bars'] == 2
    assert result['touchstone_contract']['coverage'] == {'requested': 1, 'computed': 1, 'failed': 0}
    assert result['touchstone_contract']['signal_field'] == 'bottom_alert'
    assert result['touchstone_contract']['formal_confirmation_only'] is True
