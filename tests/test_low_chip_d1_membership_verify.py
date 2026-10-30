"""Offline membership verification contract for the host release script."""
import json
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from test_low_chip_release_cron import LOCAL_HERMES_READABLE, load_module

pytestmark = pytest.mark.skipif(
    not LOCAL_HERMES_READABLE, reason='requires readable local Hermes release script',
)


@pytest.fixture
def membership(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, 'HISTORY_DIR', tmp_path)
    day = '2026-10-29'
    snapshot = tmp_path / f'{day}.json'
    snapshot.write_text(json.dumps({
        'data_as_of': day, 'intersection': ['000001.SZ', '600001.SH'],
    }), encoding='utf-8')
    return module, day, snapshot


def api_payload(day, codes):
    return {'ok': True, 'trade_date': day.replace('-', ''), 'count': len(codes),
            'results': [{'stock_code': code} for code in codes]}


def test_default_membership_allows_preserved_shadow_only_rows(membership):
    module, day, _ = membership
    calls = []

    def opener(request, timeout):
        calls.append(request)
        query = parse_qs(urlparse(request.full_url).query)
        # The persisted shadow-only row legitimately exists outside membership.
        codes = ['000001', '600001']
        if query.get('include_all') == ['1']:
            codes.append('300001')
        return BytesIO(json.dumps(api_payload(day, codes)).encode())

    result = module.verify_d1_api(day, 2, 'test-token', opener=opener)
    assert result['count'] == 2
    assert calls[0].get_header('Authorization') == 'Bearer test-token'
    assert parse_qs(urlparse(calls[0].full_url).query) == {'date': [day]}


def test_reordered_normalized_membership_passes(membership):
    module, day, _ = membership
    response = api_payload(day, ['600001.SH', 'SZ000001'])
    assert module.verify_d1_api(day, 2, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode())) == response


@pytest.mark.parametrize('snapshot', [
    None, {}, [], {'data_as_of': '2026-10-28', 'intersection': []},
    {'data_as_of': '2026-10-29', 'intersection': {}},
    {'data_as_of': '2026-10-29', 'intersection': ['000001.SZ', '000001']},
    {'data_as_of': '2026-10-29', 'intersection': ['000001.SZ']},
    {'data_as_of': '2026-10-29', 'intersection': ['bad-code', '600001.SH']},
])
def test_unavailable_or_unsupported_snapshot_fails_before_network(membership, snapshot):
    module, day, path = membership
    if snapshot is None:
        path.unlink()
    else:
        path.write_text(json.dumps(snapshot), encoding='utf-8')

    def forbidden_network(*args, **kwargs):
        pytest.fail('snapshot validation must precede API access')

    with pytest.raises(RuntimeError):
        module.verify_d1_api(day, 2, 'token', opener=forbidden_network)


@pytest.mark.parametrize('codes, message', [
    (['000001', '000001'], 'duplicate'),
    (['000001'], 'count mismatch'),
    (['000001', '600001', '300001'], 'count mismatch'),
    (['000001', 'invalid'], 'invalid stock code'),
])
def test_invalid_membership_fails_closed(membership, codes, message):
    module, day, _ = membership
    response = api_payload(day, codes)
    with pytest.raises(RuntimeError, match=message):
        module.verify_d1_api(day, 2, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode()))


def test_empty_membership_is_verified_explicitly(membership):
    module, day, path = membership
    path.write_text(json.dumps({'data_as_of': day, 'intersection': []}), encoding='utf-8')
    response = api_payload(day, [])
    assert module.verify_d1_api(day, 0, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode())) == response


def test_history_checks_each_snapshot_with_three_argument_verifier(membership):
    module, day, path = membership
    second_day = '2026-10-30'
    (path.parent / f'{second_day}.json').write_text(json.dumps({
        'data_as_of': second_day, 'intersection': ['300001.SZ'],
    }), encoding='utf-8')
    index = path.parent / 'index.json'
    index.write_text(json.dumps({'items': [
        {'date': day, 'intersection_count': 2},
        {'date': second_day, 'intersection_count': 1},
    ]}), encoding='utf-8')
    calls = []

    def opener(request, timeout):
        requested_day = parse_qs(urlparse(request.full_url).query)['date'][0]
        calls.append(requested_day)
        codes = ['000001', '600001'] if requested_day == day else ['300001']
        return BytesIO(json.dumps(api_payload(requested_day, codes)).encode())

    def verifier(date, count, token):
        return module.verify_d1_api(date, count, token, opener=opener)

    assert module.verify_history_index('token', index_file=index, verifier=verifier) == {'dates': 2, 'rows': 3}
    assert calls == [day, second_day]


@pytest.mark.parametrize('bad_code', ['000001.BAD', '000001.SZ.EXTRA', 600001.5, True, '1', '000001.SZ '])
@pytest.mark.parametrize('side', ['snapshot', 'api'])
def test_malformed_identifiers_are_rejected_before_normalizing(membership, bad_code, side):
    module, day, path = membership
    response = api_payload(day, ['000001', '600001'])
    if side == 'snapshot':
        path.write_text(json.dumps({'data_as_of': day, 'intersection': [bad_code, '600001.SH']}))
    else:
        response['results'][0]['stock_code'] = bad_code
    with pytest.raises(RuntimeError, match='invalid stock code'):
        module.verify_d1_api(day, 2, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode()))


def test_same_count_wrong_stock_code_fails(membership):
    module, day, _ = membership
    response = api_payload(day, ['000001', '600002'])
    with pytest.raises(RuntimeError, match='stock code set mismatch'):
        module.verify_d1_api(day, 2, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode()))


@pytest.mark.parametrize('results', [None, {}, [None, None], ['000001', '600001']])
def test_malformed_api_rows_fail_closed(membership, results):
    module, day, _ = membership
    response = api_payload(day, ['000001', '600001'])
    response['results'] = results
    with pytest.raises(RuntimeError, match='D1 API'):
        module.verify_d1_api(day, 2, 'token', opener=lambda *a, **k: BytesIO(json.dumps(response).encode()))
