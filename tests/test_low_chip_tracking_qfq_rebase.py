"""Offline regression using archived 2026-11-23 incident inputs."""
import copy
import datetime
import hashlib
import importlib.util
import json
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / 'fixtures' / 'low_chip_qfq_rebase.json'


def load_module():
    spec = importlib.util.spec_from_file_location('tracking_qfq_rebase', ROOT / 'scripts/update_low_chip_tracking.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('live I/O blocked in QFQ regression')
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(subprocess, 'run', blocked)
    monkeypatch.setattr(subprocess, 'Popen', blocked)


def inputs(symbol='000582.SZ', provider='THS'):
    data = json.loads(FIXTURE.read_text())[symbol]
    stored = data['stored']
    bars = data['ths'] if provider == 'THS' else [
        {'date': r['date'], 'close': float(r['close']), 'change_pct': None}
        for r in data['baostock']['qfq_rows'] if r['date'] >= stored[0]['date']
    ]
    rec = {'first_seen': stored[0]['date'], 'last_seen': stored[0]['date'], 'daily': stored,
           'entry_features': {'immutable': 'dated entry'}, 'entry_financials': {'roe': 17},
           'entry_technical': {'close': stored[0]['close']}, 'entry_halt': {'audit': 'entry'}}
    contract = {
        'schema': 'low-chip-qfq-rebase-v1', 'symbol': symbol, 'provider': provider,
        'adjustment': 'qfq', 'previous_basis_as_of': stored[-1]['date'],
        'basis_as_of': '2026-11-23', 'stored_sha256': digest(stored), 'prices_sha256': digest(bars),
        'required_dates': [r['date'] for r in bars],
        'corporate_actions': {'source': 'baostock_adjust_factor', 'symbol': symbol,
            'start': stored[-1]['date'], 'end': '2026-11-23', 'error_code': '0',
            'complete': True, 'rows': data['baostock']['factors']},
    }
    return rec, bars, contract


@pytest.mark.parametrize('symbol', ['000582.SZ', '603383.SH', '002007.SZ'])
@pytest.mark.parametrize('provider', ['THS', 'BaoStock'])
def test_real_incident_whole_window_rebase(symbol, provider):
    m = load_module()
    rec, bars, contract = inputs(symbol, provider)
    before = copy.deepcopy(rec)
    with pytest.raises(ValueError, match='QFQ overlap'):
        m._merge_prices(rec['daily'], [], bars)
    result, provenance = m.validated_qfq_rebase(symbol, rec, bars, provider, '2026-11-23', contract)
    assert rec == before
    assert [r['date'] for r in result] == [r['date'] for r in bars]
    assert [r['close'] for r in result] == [r['close'] for r in bars]
    assert all(r['profit_ratio'] == before['daily'][i]['profit_ratio'] for i, r in enumerate(result[:len(before['daily'])]))
    assert result[0]['change_pct'] is None
    for prev, row in zip(result, result[1:]):
        assert row['change_pct'] == round((row['close'] - prev['close']) / prev['close'] * 100, 2)
    assert provenance['provider'] == provider
    assert provenance['as_of'] == '2026-11-23'
    assert provenance['rebase']['stored_sha256'] == contract['stored_sha256']
    assert m._merge_prices(result, [], bars) == []


def mock_main(m, monkeypatch, tmp_path, rec, bars, provider):
    symbol = '000582.SZ'
    before = copy.deepcopy(rec)
    path = tmp_path / 'tracking.json'
    path.write_text(json.dumps({'stocks': {symbol: rec}}))
    monkeypatch.setattr(m, 'DATA', path)
    monkeypatch.setattr(m, 'HISTORY_DIR', tmp_path / 'snapshots')
    monkeypatch.setattr(m, 'load_history_dates', lambda: {symbol: [rec['first_seen']]})
    monkeypatch.setattr(m.time, 'sleep', lambda *_: None)
    def tencent(*args, **kwargs):
        if provider == 'Tencent':
            assert kwargs['stored'] is None
            return copy.deepcopy(bars)
        raise RuntimeError('offline fallback')
    monkeypatch.setattr(m, 'tencent_daily', tencent)
    monkeypatch.setattr(m, 'ths_daily', lambda *args, **kwargs: copy.deepcopy(bars) if provider == 'THS' else [])
    monkeypatch.setattr(m, 'baostock_daily_batch', lambda *args, **kwargs: {symbol: copy.deepcopy(bars)} if provider == 'BaoStock' else {})
    monkeypatch.setattr(m, 'chip_list_profit_ratios', lambda *args, **kwargs: {r['date'].replace('-', ''): 99.0 for r in bars})
    monkeypatch.setattr(m, 'confirmed_suspensions', lambda *_: {})
    monkeypatch.setattr(m, 'load_entry_enrichment', lambda *args: ({'financials': {'new': 0}}, {}))
    monkeypatch.setattr(m, 'resolve_entry_snapshot', lambda *args: None)
    return path, before


@pytest.mark.parametrize('provider', ['Tencent', 'THS', 'BaoStock'])
def test_main_rebases_single_source_and_preserves_entry_evidence(provider, monkeypatch, tmp_path):
    m = load_module()
    rec, bars, contract = inputs(provider='BaoStock' if provider == 'BaoStock' else 'THS')
    contract['provider'] = provider
    path, before = mock_main(m, monkeypatch, tmp_path, rec, bars, provider)
    assert m.main(target_day='2026-11-23', skip_iwencai=True,
                  qfq_rebase_contracts={'000582.SZ': contract}) == 0
    out = json.loads(path.read_text())['stocks']['000582.SZ']
    assert [r['close'] for r in out['daily']] == [r['close'] for r in bars]
    assert [r['profit_ratio'] for r in out['daily'][:len(before['daily'])]] == [r['profit_ratio'] for r in before['daily']]
    for field in ('entry_features', 'entry_financials', 'entry_technical', 'entry_halt'):
        assert out[field] == before[field]
    assert out['price_basis']['provider'] == provider
    assert out['price_basis']['rebase'] == contract
    assert out['daily'][0]['change_pct'] is None
    for prev, row in zip(out['daily'], out['daily'][1:]):
        assert row['change_pct'] == round((row['close'] - prev['close']) / prev['close'] * 100, 2)


@pytest.mark.parametrize('provider', ['THS', 'BaoStock'])
@pytest.mark.parametrize('mode', ['valid', 'invalid', 'default'])
def test_contract_provider_runs_after_tencent_already_covers_target(provider, mode, monkeypatch, tmp_path):
    m = load_module()
    rec, bars, contract = inputs(provider=provider)
    path, before = mock_main(m, monkeypatch, tmp_path, rec, bars, provider)
    old_closes = {row['date']: row['close'] for row in before['daily']}
    tencent_bars = [{**row, 'close': old_closes.get(row['date'], row['close'])} for row in bars]
    calls = []
    monkeypatch.setattr(m, 'tencent_daily', lambda *args, **kwargs: copy.deepcopy(tencent_bars))

    def selected(*args, **kwargs):
        calls.append(provider)
        return copy.deepcopy(bars) if provider == 'THS' else {'000582.SZ': copy.deepcopy(bars)}

    monkeypatch.setattr(m, 'ths_daily' if provider == 'THS' else 'baostock_daily_batch', selected)
    original_bytes = path.read_bytes()
    if mode == 'invalid':
        contract['prices_sha256'] = '0' * 64
    contracts = None if mode == 'default' else {'000582.SZ': contract}
    status = m.main(target_day='2026-11-23', skip_iwencai=True, qfq_rebase_contracts=contracts)
    assert calls == ([] if mode == 'default' else [provider])
    if mode == 'invalid':
        assert status == 1
        assert path.read_bytes() == original_bytes
        return
    assert status == 0
    out = json.loads(path.read_text())['stocks']['000582.SZ']
    if mode == 'default':
        assert [row['close'] for row in out['daily']] == [row['close'] for row in tencent_bars]
        assert 'price_basis' not in out
        return
    assert [row['close'] for row in out['daily']] == [row['close'] for row in bars]
    assert out['price_basis']['rebase'] == contract
    assert [row['profit_ratio'] for row in out['daily'][:len(before['daily'])]] == [row['profit_ratio'] for row in before['daily']]


@pytest.mark.parametrize('provider', ['Tencent', 'THS', 'BaoStock'])
def test_completed_window_executes_explicit_rebase_contract(provider, monkeypatch, tmp_path):
    m = load_module()
    rec, _, contract = inputs()
    end = datetime.date(2026, 11, 16)
    days = []
    while len(days) < m.MAX_STORED_BARS:
        if end.weekday() < 5:
            days.append(end.isoformat())
        end -= datetime.timedelta(days=1)
    days.reverse()
    rec['first_seen'] = rec['last_seen'] = days[0]
    rec['daily'] = [{'date': day, 'close': 10.0, 'change_pct': None if i == 0 else 0.0,
                     'profit_ratio': float(i)} for i, day in enumerate(days)]
    rec['tracking_complete'] = True
    bars = [{'date': day, 'close': 9.0, 'change_pct': None} for day in days]
    contract.update(provider=provider, previous_basis_as_of=days[-1],
                    stored_sha256=digest(rec['daily']), prices_sha256=digest(bars), required_dates=days)
    contract['corporate_actions']['start'] = days[-1]
    contract['corporate_actions']['rows'][0]['dividOperateDate'] = '2026-11-20'
    replacement, _ = m.validated_qfq_rebase('000582.SZ', rec, bars, provider, '2026-11-23', contract)
    path, before = mock_main(m, monkeypatch, tmp_path, rec, bars, provider)
    assert m.main(target_day='2026-11-23', skip_iwencai=True,
                  qfq_rebase_contracts={'000582.SZ': contract}) == 0
    out = json.loads(path.read_text())['stocks']['000582.SZ']
    assert out['daily'] == replacement
    assert out['tracking_complete'] is True
    assert out['price_basis']['rebase'] == contract
    for field in ('entry_features', 'entry_financials', 'entry_technical', 'entry_halt'):
        assert out[field] == before[field]


@pytest.mark.parametrize('mutation', [
    'no_contract', 'provider', 'symbol', 'adjustment', 'stale_hash', 'response_hash',
    'missing_old', 'missing_middle', 'missing_target', 'duplicate', 'nan', 'bool',
    'no_actions', 'action_error', 'action_partial', 'action_symbol', 'action_old',
    'action_future', 'action_duplicate', 'action_nan', 'basis_future', 'prior_basis',
])
def test_contract_rejects_unsafe_rebases(mutation):
    m = load_module()
    rec, bars, contract = inputs()
    if mutation == 'no_contract': contract = None
    elif mutation == 'provider': contract['provider'] = 'BaoStock'
    elif mutation == 'symbol': contract['symbol'] = '002007.SZ'
    elif mutation == 'adjustment': contract['adjustment'] = 'raw'
    elif mutation == 'stale_hash': contract['stored_sha256'] = '0' * 64
    elif mutation == 'response_hash': contract['prices_sha256'] = '0' * 64
    elif mutation == 'missing_old': bars.pop(0)
    elif mutation == 'missing_middle': bars.pop(-2)
    elif mutation == 'missing_target': bars.pop()
    elif mutation == 'duplicate': bars.insert(1, copy.deepcopy(bars[0]))
    elif mutation == 'nan': bars[0]['close'] = 'NaN'
    elif mutation == 'bool': bars[0]['close'] = True
    elif mutation == 'no_actions': contract['corporate_actions']['rows'] = []
    elif mutation == 'action_error': contract['corporate_actions']['error_code'] = '1'
    elif mutation == 'action_partial': contract['corporate_actions']['complete'] = False
    elif mutation == 'action_symbol': contract['corporate_actions']['rows'][0]['code'] = 'sh.603383'
    elif mutation == 'action_old': contract['corporate_actions']['rows'][0]['dividOperateDate'] = '2026-11-16'
    elif mutation == 'action_future': contract['corporate_actions']['rows'][0]['dividOperateDate'] = '2026-11-24'
    elif mutation == 'action_duplicate': contract['corporate_actions']['rows'] *= 2
    elif mutation == 'action_nan': contract['corporate_actions']['rows'][0]['adjustFactor'] = 'NaN'
    elif mutation == 'basis_future': contract['basis_as_of'] = '2026-11-24'
    elif mutation == 'prior_basis': rec['price_basis'] = {'adjustment': 'qfq', 'as_of': '2026-11-17'}
    if mutation in ('missing_old', 'missing_middle', 'missing_target', 'duplicate', 'nan', 'bool'):
        contract['prices_sha256'] = digest(bars)
    before = copy.deepcopy(rec)
    with pytest.raises(ValueError, match='QFQ rebase rejected'):
        m.validated_qfq_rebase('000582.SZ', rec, bars, 'THS', '2026-11-23', contract)
    assert rec == before


@pytest.mark.parametrize('mode', ['default', 'partial', 'bad_action', 'wrong_source', 'unknown_symbol'])
def test_main_failures_preserve_exact_file(mode, monkeypatch, tmp_path):
    m = load_module()
    rec, bars, contract = inputs()
    if mode == 'partial':
        bars.pop(0)
        contract['prices_sha256'] = digest(bars)
    if mode == 'bad_action': contract['corporate_actions']['complete'] = False
    if mode == 'wrong_source': contract['provider'] = 'BaoStock'
    path, _ = mock_main(m, monkeypatch, tmp_path, rec, bars, 'THS')
    old_bytes = path.read_bytes()
    contracts = None if mode == 'default' else {'000582.SZ': contract}
    if mode == 'unknown_symbol': contracts = {'999999.SZ': contract}
    assert m.main(target_day='2026-11-23', skip_iwencai=True, qfq_rebase_contracts=contracts) == 1
    assert path.read_bytes() == old_bytes


def test_same_basis_target_requires_no_rebase_contract(monkeypatch, tmp_path):
    m = load_module()
    rec, bars, _ = inputs()
    for row in bars:
        old = next((r for r in rec['daily'] if r['date'] == row['date']), None)
        if old:
            row['close'] = old['close']
    path, before = mock_main(m, monkeypatch, tmp_path, rec, bars, 'THS')
    assert m.main(target_day='2026-11-23', skip_iwencai=True) == 0
    out = json.loads(path.read_text())['stocks']['000582.SZ']
    assert out['daily'][:len(before['daily'])] == before['daily']
    assert 'price_basis' not in out


def test_rebase_validates_stored_rows_before_replacement():
    m = load_module()
    rec, bars, contract = inputs()
    rec['daily'][0]['close'] = True
    contract['stored_sha256'] = digest(rec['daily'])
    with pytest.raises(ValueError, match='stored rows invalid'):
        m.validated_qfq_rebase('000582.SZ', rec, bars, 'THS', '2026-11-23', contract)


def test_followup_rebase_uses_latest_stored_cutoff_with_prior_provenance():
    m = load_module()
    rec, bars, contract = inputs()
    rec['price_basis'] = {'provider': 'THS', 'adjustment': 'qfq', 'as_of': '2026-11-13',
                          'rebase': {'audit': 'earlier basis'}}
    result, provenance = m.validated_qfq_rebase('000582.SZ', rec, bars, 'THS', '2026-11-23', contract)
    assert provenance['previous_basis'] == rec['price_basis']
    assert result[-1]['date'] == '2026-11-23'


def test_rebase_keeps_global_atomic_write_when_another_symbol_fails(monkeypatch, tmp_path):
    m = load_module()
    rec, bars, contract = inputs()
    other, _, _ = inputs('603383.SH')
    path, _ = mock_main(m, monkeypatch, tmp_path, rec, bars, 'THS')
    path.write_text(json.dumps({'stocks': {'000582.SZ': rec, '603383.SH': other}}))
    old = path.read_bytes()
    monkeypatch.setattr(m, 'ths_daily', lambda symbol, *args, **kw: copy.deepcopy(bars) if symbol == '000582.SZ' else [])
    assert m.main(target_day='2026-11-23', skip_iwencai=True,
                  qfq_rebase_contracts={'000582.SZ': contract}) == 1
    assert path.read_bytes() == old


def test_tencent_parser_gate_bypassed_only_with_downstream_contract():
    m = load_module()
    rec, bars, contract = inputs()
    contract['provider'] = 'Tencent'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self):
            return json.dumps({'code': 0, 'data': {'sz000582': {'qfqday': [
                [r['date'], str(r['close']), str(r['close']), str(r['close']), str(r['close']), '100']
                for r in bars]}}}).encode()
    def fetch(stored):
        return m.tencent_daily('000582.SZ', rec['first_seen'], '2026-11-23',
                               stored=stored, opener=lambda *args, **kw: Response(), sleeper=lambda *_: None)
    with pytest.raises(RuntimeError, match='incompatible with stored'):
        fetch(rec['daily'])
    incoming = fetch(None)
    contract['prices_sha256'] = digest(incoming)
    result, _ = m.validated_qfq_rebase('000582.SZ', rec, incoming, 'Tencent', '2026-11-23', contract)
    assert [r['close'] for r in result] == [r['close'] for r in bars]
    with pytest.raises(ValueError, match='explicit contract missing'):
        m.validated_qfq_rebase('000582.SZ', rec, incoming, 'Tencent', '2026-11-23', None)
