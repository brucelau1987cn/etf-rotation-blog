import json

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
