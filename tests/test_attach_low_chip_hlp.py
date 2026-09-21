import json
import multiprocessing
import os
import signal
import time

import pandas as pd
import pytest

from scripts import attach_low_chip_hlp as module


class CFClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def klines(self, symbols, start, end, *, fields):
        self.calls.append((symbols, start, end, fields))
        return {symbols[0]: self.rows}


def test_cf_history_is_preferred_and_requests_hlp_fields(monkeypatch):
    client = CFClient([
            {'date': '2026-09-14', 'high': 11, 'low': 9, 'close': 10, 'turn': 1.2, 'tradestatus': '1'},
            {'date': '2026-09-15', 'high': 12, 'low': 10, 'close': 11, 'turn': 1.3, 'tradestatus': '0'},
    ])
    monkeypatch.setattr(module, 'fetch_local_baostock', lambda *_: pytest.fail('local BaoStock called'))

    frame = module.fetch('000001.SZ', '2026-09-01', '2026-09-15', client=client)

    assert frame.to_dict('records') == [
        {'date': '2026-09-14', 'high': 11, 'low': 9, 'close': 10, 'turn': 1.2, 'tradestatus': '1'},
    ]
    assert client.calls == [(
        ['000001.SZ'], '2026-09-01', '2026-09-15',
        ('date', 'high', 'low', 'close', 'turn', 'tradestatus'),
    )]


def test_cf_failure_falls_back_to_local_baostock(monkeypatch):
    class BrokenClient:
        def klines(self, *_args, **_kwargs):
            raise RuntimeError('gateway down')

    expected = pd.DataFrame([{
        'date': '2026-09-14', 'high': 11.0, 'low': 9.0, 'close': 10.0,
        'turn': 1.2, 'tradestatus': '1',
    }])
    monkeypatch.setattr(module, 'fetch_local_baostock', lambda *_: expected)

    result = module.fetch('000001.SZ', '2026-09-01', '2026-09-15', client=BrokenClient())
    pd.testing.assert_frame_equal(result, expected)


def test_cf_batch_fetches_up_to_five_symbols_once(monkeypatch):
    rows = [
        {'date': '2026-09-14', 'high': 11, 'low': 9, 'close': 10, 'turn': 1.2, 'tradestatus': '1'},
    ]
    client = CFClient(rows)
    client.klines = lambda symbols, start, end, *, fields: (
        client.calls.append((symbols, start, end, fields)) or {symbol: rows for symbol in symbols}
    )

    frames = module.fetch_cf_batch(
        ['000001.SZ', '000002.SZ'], '2026-09-01', '2026-09-15', client=client,
    )

    assert set(frames) == {'000001.SZ', '000002.SZ'}
    assert len(client.calls) == 1
    assert client.calls[0][0] == ['000001.SZ', '000002.SZ']


def test_build_batches_cf_requests_and_publishes_complete_coverage(tmp_path, monkeypatch):
    codes = [f'{index:06d}.SZ' for index in range(1, 8)]
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({
        'data_as_of': '2026-09-15', 'intersection': codes,
        'enrichments': {code: {} for code in codes},
    }), encoding='utf-8')
    rows = [
        {'date': f'2026-01-{(index % 28) + 1:02d}', 'high': 11, 'low': 9,
         'close': 10, 'turn': 1, 'tradestatus': '1'}
        for index in range(100)
    ]
    calls = []

    class Client:
        def klines(self, symbols, start, end, *, fields):
            calls.append(list(symbols))
            return {symbol: rows for symbol in symbols}

    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('THS unavailable')))
    monkeypatch.setattr(module, 'fetch_local_batch', lambda *_args, **_kwargs: ({}, {}))
    monkeypatch.setattr(module, 'calc', lambda _frame: {'hlp': 1, 'chip_signals': []})
    coverage = module.build_and_publish(target, client=Client())

    assert calls == [codes[:5], codes[5:]]
    assert coverage == {'requested': 7, 'computed': 7, 'failed': 0}


def test_ths_chip_list_builds_daily_profit_series(monkeypatch):
    payload = {
        'status_code': 0,
        'data': {'list': {
            '20260915': {
                'summary': {'close_price': 10, 'average_cost': 9.5},
                'curve_data': {'list': [
                    {'price': 9, 'jeton': 3},
                    {'price': 10, 'jeton': 2},
                    {'price': 11, 'jeton': 5},
                ]},
            },
            '20260916': {
                'summary': {'close_price': 9, 'average_cost': 9.8},
                'curve_data': {'list': [
                    {'price': 9, 'jeton': 1},
                    {'price': 10, 'jeton': 9},
                ]},
            },
        }},
    }

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return json.dumps(payload).encode()

    captured = {}
    def opener(request, timeout=20):
        captured['url'] = request.full_url
        captured['timeout'] = timeout
        return Response()

    result = module.fetch_ths_chip_profit_series(
        '600000.SH', '2026-01-01', '2026-09-16', opener=opener,
    )

    assert result == [
        {'date': '2026-09-15', 'profit_ratio': 50.0, 'close': 10.0, 'average_cost': 9.5},
        {'date': '2026-09-16', 'profit_ratio': 10.0, 'close': 9.0, 'average_cost': 9.8},
    ]
    assert 'stock_market=17' in captured['url']
    assert captured['timeout'] == 20


def test_hlp_metrics_can_be_calculated_from_official_ths_profit_series():
    series = [
        {'date': f'2026-01-{(index % 28) + 1:02d}', 'profit_ratio': float(index), 'close': 10, 'average_cost': 9}
        for index in range(1, 101)
    ]
    result = module.calc_profit_series(series)
    assert result['hlp'] == 100.0
    assert result['hlp15'] == 93.0
    assert result['hlp60'] == 70.5
    assert result['hlp100'] == 50.5
    assert result['average_cost'] == 9.0


def test_ths_cache_accumulates_until_100_sessions(tmp_path):
    cache_path = tmp_path / 'series.json'
    cached = [
        {'date': f'2025-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}', 'profit_ratio': 2.0,
         'close': 10.0, 'average_cost': 9.0}
        for index in range(80)
    ]
    fresh = [
        {'date': f'2026-09-{index + 1:02d}', 'profit_ratio': 3.0, 'close': 11.0, 'average_cost': 10.0}
        for index in range(20)
    ]
    module.save_series_cache({'600000.SH': cached}, cache_path)
    loaded = module.load_series_cache(cache_path)
    merged = module.merge_profit_series(loaded['600000.SH'], fresh)
    assert len(merged) == 100
    assert module.calc_profit_series(merged)['hlp100'] == 2.2


def test_stale_ths_series_enters_fallback(tmp_path, monkeypatch):
    code = '600000.SH'
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({'data_as_of': '2026-09-17', 'intersection': [code], 'enrichments': {code: {}}}))
    series = [
        {'date': f'2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}', 'profit_ratio': 1.0,
         'close': 10.0, 'average_cost': 9.0}
        for index in range(100)
    ]
    series[-1]['date'] = '2026-09-16'
    frame = pd.DataFrame([
        {'date': f'2026-01-{(index % 28) + 1:02d}', 'high': 11, 'low': 9, 'close': 10, 'turn': 1, 'tradestatus': '1'}
        for index in range(100)
    ])
    frame.attrs['source'] = 'local-baostock'
    monkeypatch.setattr(module, 'SERIES_CACHE', tmp_path / 'cache.json')
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda *_args, **_kwargs: series)
    monkeypatch.setattr(module, 'fetch_local_batch', lambda *_args, **_kwargs: ({code: frame}, {}))
    monkeypatch.setattr(module, 'calc', lambda df: {'hlp': 1, 'source': df.attrs['source'], 'chip_signals': []})
    coverage = module.build_and_publish(target)
    assert coverage['computed'] == 1
    assert json.loads(target.read_text())['enrichments'][code]['hlp_metrics']['source'] == 'local-baostock'


def test_empty_ths_fresh_with_full_stale_cache_enters_fallback(tmp_path, monkeypatch):
    code = '600000.SH'
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({'data_as_of': '2026-09-17', 'intersection': [code], 'enrichments': {code: {}}}))
    cached = [
        {'date': f'2025-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}', 'profit_ratio': 1.0,
         'close': 10.0, 'average_cost': 9.0}
        for index in range(100)
    ]
    cached[-1]['date'] = '2026-09-16'
    cache_path = tmp_path / 'cache.json'
    module.save_series_cache({code: cached}, cache_path)
    frame = pd.DataFrame([
        {'date': f'2026-01-{(index % 28) + 1:02d}', 'high': 11, 'low': 9, 'close': 10, 'turn': 1, 'tradestatus': '1'}
        for index in range(100)
    ])
    frame.attrs['source'] = 'local-baostock'
    monkeypatch.setattr(module, 'SERIES_CACHE', cache_path)
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, 'fetch_local_batch', lambda *_args, **_kwargs: ({code: frame}, {}))
    monkeypatch.setattr(module, 'calc', lambda df: {'hlp': 1, 'source': df.attrs['source'], 'chip_signals': []})
    coverage = module.build_and_publish(target)
    assert coverage == {'requested': 1, 'computed': 1, 'failed': 0}
    assert json.loads(target.read_text())['enrichments'][code]['hlp_metrics']['source'] == 'local-baostock'


def test_local_partial_failure_preserves_success_and_cf_only_fetches_missing(monkeypatch):
    codes = ['600000.SH', '000001.SZ']
    good = pd.DataFrame([{'date': '2026-09-17'}])
    good.attrs['source'] = 'local-baostock'
    monkeypatch.setattr(module, 'fetch_local_batch', lambda *_args, **_kwargs: ({codes[0]: good}, {codes[1]: 'boom'}))
    calls = []
    class Client:
        def klines(self, symbols, *_args, **_kwargs):
            calls.append(list(symbols))
            return {codes[1]: [{'date': '2026-09-17', 'high': 2, 'low': 1, 'close': 1.5, 'turn': 1, 'tradestatus': '1'}]}
    frames, _errors = module._baostock_fallback(codes, '2026-01-01', '2026-09-17', client=Client())
    assert set(frames) == set(codes)
    assert calls == [[codes[1]]]
    assert frames[codes[0]].attrs['source'] == 'local-baostock'
    assert frames[codes[1]].attrs['source'] == 'cf-baostock'


def test_build_prefers_ths_and_does_not_call_baostock(tmp_path, monkeypatch):
    codes = ['600000.SH', '000001.SZ']
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({
        'data_as_of': '2026-09-16', 'intersection': codes,
        'enrichments': {code: {} for code in codes},
    }), encoding='utf-8')
    series = [
        {'date': f'2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}', 'profit_ratio': 1.0,
         'close': 10.0, 'average_cost': 9.0}
        for index in range(100)
    ]
    series[-1]['date'] = '2026-09-16'
    monkeypatch.setattr(module, 'SERIES_CACHE', tmp_path / 'series-cache.json')
    assert not module.SERIES_CACHE.exists()
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda symbol, *_args, **_kwargs: list(series))
    monkeypatch.setattr(module, 'fetch_local_batch', lambda *_args, **_kwargs: pytest.fail('local BaoStock called'))
    monkeypatch.setattr(module, 'fetch_cf_batch', lambda *_args, **_kwargs: pytest.fail('CF BaoStock called'))

    coverage = module.build_and_publish(target)
    saved = json.loads(target.read_text())
    assert module.SERIES_CACHE.exists()
    assert set(module.load_series_cache(module.SERIES_CACHE)) == set(codes)

    assert coverage == {'requested': 2, 'computed': 2, 'failed': 0}
    assert saved['hlp_contract']['source'].startswith('同花顺官方 chip-list')
    assert all(saved['enrichments'][code]['hlp_metrics']['hlp100'] == 1.0 for code in codes)


def test_incomplete_hlp_coverage_fails_closed_and_preserves_file(tmp_path):
    original = {
        'data_as_of': '2026-09-15',
        'intersection': ['000001.SZ', '000002.SZ'],
        'enrichments': {
            '000001.SZ': {'hlp_metrics': {'stale': True}},
            '000002.SZ': {'hlp_metrics': {'stale': True}},
        },
    }
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps(original), encoding='utf-8')
    good = pd.DataFrame([
        {'date': f'2026-01-{(index % 28) + 1:02d}', 'high': 11.0, 'low': 9.0,
         'close': 10.0, 'turn': 1.0, 'tradestatus': '1'}
        for index in range(100)
    ])

    with pytest.raises(RuntimeError, match='coverage incomplete'):
        module.build_and_publish(
            target,
            history_loader=lambda symbol, _start, _end: good if symbol == '000001.SZ' else pd.DataFrame(),
        )

    assert json.loads(target.read_text(encoding='utf-8')) == original


def test_short_ths_series_uses_one_local_batch_without_cf_duplicate(tmp_path, monkeypatch):
    codes = ['600000.SH', '000001.SZ']
    target = tmp_path / 'stocks.json'
    original = {'data_as_of': '2026-09-17', 'intersection': codes,
                'enrichments': {code: {} for code in codes}}
    target.write_text(json.dumps(original), encoding='utf-8')
    short = [
        {'date': f'2026-08-{index + 1:02d}', 'profit_ratio': 1.0,
         'close': 10.0, 'average_cost': 9.0}
        for index in range(40)
    ]
    short[-1]['date'] = '2026-09-17'
    frame = pd.DataFrame([{'date': str(index), 'high': 11, 'low': 9, 'close': 10,
                           'turn': 1, 'tradestatus': '1'} for index in range(100)])
    frame.attrs['source'] = 'local-baostock'
    local_calls = []

    monkeypatch.setattr(module, 'SERIES_CACHE', tmp_path / 'cache.json')
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda *_args, **_kwargs: list(short))
    monkeypatch.setattr(module, 'calc', lambda df: {'hlp': 1, 'source': df.attrs['source'], 'chip_signals': []})
    monkeypatch.setattr(module, 'fetch_cf_batch', lambda *_args, **_kwargs: pytest.fail('duplicate CF path'))

    def local_runner(symbols, _start, _end, *, timeout):
        local_calls.append((list(symbols), timeout))
        return {symbol: frame.copy() for symbol in symbols}, {}

    coverage = module.build_and_publish(target, local_batch_runner=local_runner,
                                        total_budget=30, local_budget=7)

    assert coverage == {'requested': 2, 'computed': 2, 'failed': 0}
    assert local_calls == [(codes, 7)]


def test_local_stage_timeout_falls_back_to_cf_for_all_missing(monkeypatch):
    codes = ['600000.SH', '000001.SZ']
    rows = [{'date': '2026-09-17', 'high': 2, 'low': 1, 'close': 1.5,
             'turn': 1, 'tradestatus': '1'}]
    calls = []

    def timed_out_local(*_args, **_kwargs):
        raise module.StageTimeout('local-baostock timed out after 3s')

    class Client:
        def klines(self, symbols, *_args, **_kwargs):
            calls.append(list(symbols))
            return {symbol: rows for symbol in symbols}

    frames, errors = module._baostock_fallback(
        codes, '2026-01-01', '2026-09-17', client=Client(),
        local_batch_runner=timed_out_local, local_budget=3,
    )

    assert set(frames) == set(codes)
    assert calls == [codes]
    assert 'timed out after 3s' in errors['local-baostock']


def test_overall_budget_failure_names_stage_and_preserves_formal_data(tmp_path, monkeypatch):
    code = '600000.SH'
    target = tmp_path / 'stocks.json'
    original = {'data_as_of': '2026-09-17', 'intersection': [code],
                'enrichments': {code: {'hlp_metrics': {'stale': True}}}}
    target.write_text(json.dumps(original), encoding='utf-8')
    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1.2])

    monkeypatch.setattr(module, 'SERIES_CACHE', tmp_path / 'cache.json')
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series', lambda *_args, **_kwargs: [])
    fallback = pd.DataFrame([{'date': str(index)} for index in range(100)])

    with pytest.raises(module.StageTimeout, match=r'overall budget .* stage=local-baostock'):
        module.build_and_publish(
            target, total_budget=1, local_budget=1,
            clock=lambda: next(ticks),
            local_batch_runner=lambda *_args, **_kwargs: ({code: fallback}, {}),
        )

    assert json.loads(target.read_text(encoding='utf-8')) == original


def test_interruptible_local_batch_terminates_hung_worker():
    started = time.monotonic()

    with pytest.raises(module.StageTimeout, match='local-baostock timed out'):
        module.run_local_batch_interruptible(
            ['600000.SH'], '2026-01-01', '2026-09-17', timeout=0.1,
            worker=lambda *_args: time.sleep(10),
        )

    assert time.monotonic() - started < 2


def test_overall_alarm_during_local_worker_cleans_up_process_and_pipe():
    before = {process.pid for process in multiprocessing.active_children()}
    open_fds_before = len(os.listdir('/proc/self/fd'))
    previous_handler = signal.getsignal(signal.SIGALRM)

    def alarm_handler(_signum, _frame):
        raise module.OverallBudgetTimeout('test overall alarm')

    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.1)
    try:
        with pytest.raises(module.OverallBudgetTimeout, match='test overall alarm'):
            module.run_local_batch_interruptible(
                ['600000.SH'], '2026-01-01', '2026-09-17', timeout=10,
                worker=lambda *_args: time.sleep(10),
            )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

    assert {process.pid for process in multiprocessing.active_children()} == before
    assert len(os.listdir('/proc/self/fd')) == open_fds_before


def test_alarm_during_process_start_defers_delivery_and_cleans_startup_resources(monkeypatch):
    events = []
    initial_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    previous_handler = signal.getsignal(signal.SIGALRM)

    class Connection:
        def __init__(self, name):
            self.name = name
            self.closed = False

        def close(self):
            self.closed = True
            events.append(f'close-{self.name}')

    parent = Connection('parent')
    child = Connection('child')

    class Process:
        pid = None
        alive = False
        closed = False

        def start(self):
            self.pid = 12345
            self.alive = True
            events.append('start')
            signal.raise_signal(signal.SIGALRM)
            events.append('start-returned')

        def is_alive(self):
            return self.alive

        def terminate(self):
            events.append('terminate')
            self.alive = False

        def join(self, _timeout):
            events.append('join')

        def kill(self):
            pytest.fail('terminated fake worker should not need kill')

        def close(self):
            self.closed = True
            events.append('close-process')

    process = Process()

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return parent, child

        def Process(self, **_kwargs):
            return process

    def alarm_handler(_signum, _frame):
        events.append('alarm')
        raise module.OverallBudgetTimeout('alarm during process.start')

    monkeypatch.setattr(module.multiprocessing, 'get_context', lambda method: Context())
    signal.signal(signal.SIGALRM, alarm_handler)
    try:
        with pytest.raises(module.OverallBudgetTimeout, match='alarm during process.start'):
            module.run_local_batch_interruptible(
                ['600000.SH'], '2026-01-01', '2026-09-17', timeout=10,
            )
    finally:
        signal.signal(signal.SIGALRM, previous_handler)

    assert events[:3] == ['start', 'start-returned', 'close-child']
    assert events[3:] == ['alarm', 'close-parent', 'close-child',
                          'terminate', 'join', 'close-process']
    assert parent.closed and child.closed and process.closed
    assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == initial_mask


def test_alarm_after_atomic_replace_reports_publish_success(tmp_path, monkeypatch):
    code = '600000.SH'
    target = tmp_path / 'stocks.json'
    target.write_text(json.dumps({
        'data_as_of': '2026-09-17', 'intersection': [code],
        'enrichments': {code: {'hlp_metrics': {'stale': True}}},
    }), encoding='utf-8')
    frame = pd.DataFrame([{'date': str(index)} for index in range(100)])
    real_replace = os.replace

    def replace_then_alarm(source, destination):
        real_replace(source, destination)
        signal.raise_signal(signal.SIGALRM)

    monkeypatch.setattr(module.os, 'replace', replace_then_alarm)
    monkeypatch.setattr(module, 'calc', lambda _frame: {'hlp': 1, 'chip_signals': []})

    coverage = module.build_and_publish(
        target, history_loader=lambda *_args: frame, total_budget=30,
    )

    assert coverage == {'requested': 1, 'computed': 1, 'failed': 0}
    assert json.loads(target.read_text(encoding='utf-8'))['enrichments'][code]['hlp_metrics']['hlp'] == 1


def test_overall_hard_budget_interrupts_hung_ths_and_preserves_file(tmp_path, monkeypatch):
    code = '600000.SH'
    target = tmp_path / 'stocks.json'
    original = {'data_as_of': '2026-09-17', 'intersection': [code],
                'enrichments': {code: {'hlp_metrics': {'stale': True}}}}
    target.write_text(json.dumps(original), encoding='utf-8')
    monkeypatch.setattr(module, 'SERIES_CACHE', tmp_path / 'cache.json')
    monkeypatch.setattr(module, 'fetch_ths_chip_profit_series',
                        lambda *_args, **_kwargs: time.sleep(10))
    started = time.monotonic()

    with pytest.raises(module.StageTimeout, match=r'overall budget .* stage=ths-chip'):
        module.build_and_publish(target, total_budget=0.1)

    assert time.monotonic() - started < 2
    assert json.loads(target.read_text(encoding='utf-8')) == original
