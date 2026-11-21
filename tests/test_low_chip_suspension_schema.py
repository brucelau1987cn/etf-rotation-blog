"""Offline transport fixtures retain the authentic Eastmoney parser/schema."""
import importlib.util
import json
import socket
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/eastmoney-suspensions-2026-11-20.json'
DAY = '2026-11-20'


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location('suspension_schema', ROOT / 'scripts/update_low_chip_tracking.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def blocked(*a, **kw):
        pytest.fail('live I/O forbidden')
    for target, name in [(socket, 'getaddrinfo'), (socket.socket, 'connect'),
                         (subprocess, 'run'), (subprocess, 'Popen')]:
        monkeypatch.setattr(target, name, blocked)
    return module


def transport(mod, monkeypatch, payload, mutate_url=lambda url: url):
    requests = []
    class Response(BytesIO):
        def geturl(self):
            return mutate_url(requests[-1])
    def open_response(request, **kwargs):
        requests.append(request.full_url)
        return Response(json.dumps(payload).encode())
    monkeypatch.setattr(mod.urllib.request, 'urlopen', open_response)
    return requests


def test_authentic_schema_binds_target_day_to_successful_query_envelope(mod, monkeypatch):
    body = json.loads(FIXTURE.read_text())
    assert all('DATETIME' not in row for row in body['result']['data'])
    requests = transport(mod, monkeypatch, body)
    evidence = mod.confirmed_suspensions(DAY)
    assert evidence['600185.SH'] == dict(status='halted', target_day=DAY,
        reason='拟筹划重大资产重组', source='eastmoney_suspend_report')
    assert '603580.SH' not in evidence  # announced next-session halt
    assert parse_qs(urlsplit(requests[0]).query)['filter'] == [f'(MARKET="全部")(DATETIME=\'{DAY}\')']


def altered_query(url, key, value):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query[key] = [value]
    return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))


@pytest.mark.parametrize('mutate', [
    lambda u: altered_query(u, 'filter', '(MARKET="全部")(DATETIME=\'2026-11-19\')'),
    lambda u: altered_query(u, 'reportName', 'OTHER'),
    lambda u: altered_query(u, 'pageNumber', '2'),
    lambda u: u.replace('datacenter-web.eastmoney.com', 'unbound.invalid'),
    lambda u: u.replace('https:', 'http:'),
    lambda u: u.split('?')[0],
])
def test_response_query_must_remain_bound(mod, monkeypatch, mutate):
    transport(mod, monkeypatch, json.loads(FIXTURE.read_text()), mutate)
    with pytest.raises(RuntimeError, match='binding'):
        mod.confirmed_suspensions(DAY)


@pytest.mark.parametrize('key', ['filter', 'reportName', 'pageNumber', 'unexpected'])
def test_response_query_rejects_appended_blank_parameter(mod, monkeypatch, key):
    transport(mod, monkeypatch, json.loads(FIXTURE.read_text()),
              lambda url: url + '&' + key + '=')
    with pytest.raises(RuntimeError, match='binding'):
        mod.confirmed_suspensions(DAY)


@pytest.mark.parametrize('changes', [
    {'SUSPEND_START_TIME': DAY + ' 13:00:00'},
    {'SUSPEND_END_TIME': DAY + ' 14:59:59'},
    {'PREDICT_RESUME_DATE': DAY + ' 00:00:00'},
    {'SUSPEND_EXPIRE': '未知'}, {'SUSPEND_REASON': ' '},
    {'DATETIME': '2026-11-19 00:00:00'},
])
def test_bound_response_still_requires_full_session_evidence(mod, monkeypatch, changes):
    body = json.loads(FIXTURE.read_text())
    row = next(r for r in body['result']['data'] if r['SECUCODE'] == '600185.SH')
    body['result']['data'] = [dict(row, **changes)]
    body['result']['count'] = 1
    transport(mod, monkeypatch, body)
    assert mod.confirmed_suspensions(DAY) == {}


@pytest.mark.parametrize('mode', ['halted', 'ordinary_gap', 'positive_volume_trade'])
def test_main_uses_real_halt_parser_with_trade_veto(tmp_path, monkeypatch, mode):
    from test_low_chip_tracking_retry import load_module
    module = load_module()
    symbol, prior = '600185.SH', '2026-11-11'
    module.DATA = tmp_path / 'tracking.json'
    module.HISTORY_DIR = tmp_path / 'history'
    module.HISTORY_DIR.mkdir()
    (module.HISTORY_DIR / (prior + '.json')).write_text(json.dumps({'intersection': [symbol], 'enrichments': {symbol: {}}}))
    stored = {'date': prior, 'close': 4.28, 'profit_ratio': 12.0, 'change_pct': None}
    module.DATA.write_text(json.dumps({'stocks': {symbol: {'first_seen': prior, 'daily': [stored]}}}))
    before = module.DATA.read_bytes()
    def tencent(*a, on_target_trade=None, **kw):
        if mode == 'positive_volume_trade':
            on_target_trade()
        return []
    monkeypatch.setattr(module, 'tencent_daily', tencent)
    monkeypatch.setattr(module, 'ths_daily', lambda *a, **kw: [])
    monkeypatch.setattr(module, 'baostock_daily_batch', lambda *a, **kw: {})
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module, 'chip_list_profit_ratios', lambda *a, **kw: {})
    body = json.loads(FIXTURE.read_text())
    if mode == 'ordinary_gap':
        body['result']['data'] = []
        body['result']['count'] = 0
    transport(module, monkeypatch, body)
    result = module.main(target_day=DAY, skip_iwencai=True)
    if mode == 'halted':
        assert result == 0
        rec = json.loads(module.DATA.read_text())['stocks'][symbol]
        assert rec['coverage']['target_day'] == DAY
        assert rec['daily'] == [stored]
    else:
        assert result != 0
        assert module.DATA.read_bytes() == before
