import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import attach_low_chip_financials as attach
from audit_low_chip_fuyao import FuyaoClient


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_budget_saves_complete_payload_and_preserves_existing(tmp_path):
    clock = Clock()
    codes = ['a', 'b', 'c', 'd']
    payload = {'intersection': codes, 'qualification': {'unchanged': True},
               'enrichments': {c: {} for c in codes}}
    payload['enrichments']['d']['financials'] = {'roe': 19, 'report_period': '20260331'}
    original = copy.deepcopy(payload)
    client = Mock()

    def financials(code, report):
        clock.sleep(1)
        if code == 'a':
            return {'roe': 20}
        raise TimeoutError('deadline')

    client.financials.side_effect = financials
    attach.attach_financials(payload, client, '2026-2', budget_seconds=2, monotonic=clock)
    attach._write_atomic(tmp_path / 'result.json', payload)
    saved = json.loads((tmp_path / 'result.json').read_text())
    assert saved['intersection'] == original['intersection']
    assert saved['qualification'] == original['qualification']
    assert saved['enrichments']['a']['financials']['roe'] == 20
    assert saved['enrichments']['a']['financials']['net_margin'] is None
    assert saved['enrichments']['b']['financials']['status'] == 'UNAVAILABLE'
    assert saved['enrichments']['c']['financials']['status'] == 'UNAVAILABLE'
    assert saved['enrichments']['d']['financials']['roe'] == 19
    assert saved['financial_coverage']['covered'] == 2
    assert saved['financial_coverage']['requested'] == 4
    assert saved['financial_coverage']['failed'] == 2
    assert client.financials.call_count == 2
    assert clock.now == 2


def test_truncated_response_preserves_partial_success_and_saves(tmp_path):
    import http.client
    clock = Clock()
    codes = ['000001.SZ', '000002.SZ']
    payload = {'intersection': codes, 'enrichments': {c: {} for c in codes}}
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.side_effect = [
        json.dumps({'code': 0, 'data': {'abilities': [{'indicators': [
            {'index_id': 'index_weighted_avg_roe', 'value': 20}
        ]}]}}).encode(),
        http.client.IncompleteRead(b'partial'),
        http.client.IncompleteRead(b'partial'),
        http.client.IncompleteRead(b'partial'),
    ]
    client = FuyaoClient('offline', opener=Mock(return_value=response),
                         sleeper=clock.sleep, monotonic=clock)
    attach.attach_financials(payload, client, '2026-2', monotonic=clock)
    attach._write_atomic(tmp_path / 'result.json', payload)
    saved = json.loads((tmp_path / 'result.json').read_text())
    assert saved['enrichments'][codes[0]]['financials']['roe'] == 20
    assert saved['enrichments'][codes[1]]['financials']['status'] == 'UNAVAILABLE'
    assert saved['enrichments'][codes[1]]['financials']['reason'] == 'IncompleteRead'
    assert saved['financial_coverage']['covered'] == 1
    assert saved['financial_coverage']['failed'] == 1
    assert saved['intersection'] == codes


def test_atomic_replace_failure_preserves_previous_payload(tmp_path, monkeypatch):
    path = tmp_path / 'result.json'
    path.write_text('{"previous":true}')
    def fail_replace(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(attach.os, 'replace', fail_replace)
    import pytest
    with pytest.raises(OSError):
        attach._write_atomic(path, {'new': True})
    assert json.loads(path.read_text()) == {'previous': True}
    assert list(tmp_path.glob('*.tmp')) == []


def test_client_caps_retry_sleep_and_request_timeout():
    clock = Clock()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"code":5003}'
    opener = Mock(return_value=response)
    client = FuyaoClient('offline', opener=opener, sleeper=clock.sleep,
                         monotonic=clock, request_timeout=0.25, deadline=1.0)
    import pytest
    with pytest.raises(TimeoutError):
        client.financials('a', '2026-2')
    assert clock.now == 1
    assert opener.call_count == 1
    assert opener.call_args.kwargs['timeout'] == 0.25


def test_client_request_wall_clock_guard():
    from unittest.mock import patch
    import audit_low_chip_fuyao as audit
    clock = Clock()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"code":0}'
    with patch.object(audit.signal, 'signal') as handler, \
         patch.object(audit.signal, 'getsignal'), \
         patch.object(audit.signal, 'setitimer', return_value=(0.0, 0.0)) as timer:
        client = FuyaoClient('offline', opener=Mock(return_value=response),
                             monotonic=clock, deadline=1.0, request_timeout=0.25)
        client.financials('a', '2026-2')
        assert timer.call_args_list[0].args == (audit.signal.ITIMER_REAL, 0.25)
        assert timer.call_args_list[1].args == (audit.signal.ITIMER_REAL, 0)
        timeout_handler = handler.call_args_list[0].args[1]
        import pytest
        with pytest.raises(TimeoutError):
            timeout_handler(None, None)
