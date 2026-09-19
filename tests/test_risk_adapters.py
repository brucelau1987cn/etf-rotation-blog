import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.attach_low_chip_risks as m

def test_request_json_retries_429_then_parses_jsonp(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'cb({"data":[1]})'
    calls = []
    def opener(_request, timeout):
        calls.append(timeout)
        if len(calls) == 1: raise urllib.error.HTTPError('x', 429, 'busy', {}, None)
        return Response()
    monkeypatch.setattr(m.time, 'sleep', lambda _seconds: None)
    assert m._request_json('https://example.test', opener=opener, min_interval=0, retries=1) == {'data': [1]}
    assert len(calls) == 2
    assert calls == [10, 10]

def test_request_json_403_opens_circuit():
    m._CIRCUIT_OPEN.clear()
    def opener(_request, timeout): raise urllib.error.HTTPError('x', 403, 'blocked', {}, None)
    with pytest.raises(RuntimeError, match='403'):
        m._request_json('https://example.test', opener=opener, min_interval=0, retries=0)
    with pytest.raises(RuntimeError, match='circuit open'):
        m._request_json('https://example.test', opener=opener, min_interval=0, retries=0)
    m._CIRCUIT_OPEN.clear()


def test_request_json_deadline_bounds_retry_sleep(monkeypatch):
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(m.time, "sleep", lambda _seconds: None)
    with pytest.raises(TimeoutError, match="budget exhausted"):
        m._request_json("https://example.test", opener=lambda *_a, **_k: None,
                        min_interval=1, deadline=1.0, retries=0)


def test_source_result_requires_date_and_reports_coverage():
    result = m._result('mock', [{'publishDate': '2026-09-18'}], as_of='2026-09-18')
    assert result['coverage_count'] == 1
    assert result['source_as_of'] == '2026-09-18'
    assert result['freshness_days'] == 0
    with pytest.raises(RuntimeError, match='no verifiable'):
        m._result('mock', [], as_of='2026-09-18')


# --- Confirmed EM contract: ann_type=A, f_node=0, s_node=0, codes[].stock_code ---


def test_announcements_uses_confirmed_contract_and_ownership(monkeypatch):
    captured = {}
    def fake(url, **kw):
        captured['url'] = url
        return {"success": 1, "data": {"list": []}}
    monkeypatch.setattr(m, '_request_json', fake)
    with pytest.raises(RuntimeError, match='no verifiable'):
        m.fetch_announcements('000157.SZ', as_of='2026-09-18')
    assert 'ann_type=A' in captured['url']
    assert 'f_node=0' in captured['url']
    assert 's_node=0' in captured['url']
    assert 'stock_list=000157' in captured['url']


def test_announcements_drops_unowned_rows(monkeypatch):
    payload = {'success': 1, 'data': {'list': [
        {'art_code': '1', 'codes': [{'stock_code': '999999'}],
         'title': '不相关', 'notice_date': '2026-09-11 00:00:00'},
        {'art_code': '2', 'codes': [{'stock_code': '000157'}],
         'title': '本公司公告', 'notice_date': '2026-09-11 00:00:00'},
    ]}}
    monkeypatch.setattr(m.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_announcements('000157.SZ', as_of='2026-09-18')
    assert result['coverage_count'] == 1


# --- Confirmed EM contract: ratings use code=... param and return hits+data[] ---

def test_ratings_uses_code_param_not_stockCode(monkeypatch):
    captured = {}
    def fake(url, **kw):
        captured['url'] = url
        return {'hits': 0, 'data': []}
    monkeypatch.setattr(m, '_request_json', fake)
    result = m.fetch_ratings('000157.SZ', as_of='2026-09-18')
    assert result['complete'] is True
    assert result['coverage_count'] == 0
    assert result['freshness'] == 'no_records'
    assert 'code=000157' in captured['url']
    assert 'stockCode=' not in captured['url']


def test_ratings_parses_hits_and_data(monkeypatch):
    payload = {'hits': 5, 'data': [
        {'stockCode': '000157', 'publishDate': '2026-09-05 00:00:00', 'title': 'A'},
        {'stockCode': '000157', 'publishDate': '2026-09-01 00:00:00', 'title': 'B'},
    ]}
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_ratings('000157.SZ', as_of='2026-09-18')
    assert result['coverage_count'] == 2
    assert result['source'] == 'Eastmoney analyst ratings'


# --- Confirmed EM contract: news uses type=["cmsArticleWebOld"] and result.cmsArticleWebOld[] ---

def test_news_uses_cmsArticleWebOld_contract(monkeypatch):
    captured = {}
    def fake(url, **kw):
        captured['url'] = url
        return {'hitsTotal': 0, 'result': {'cmsArticleWebOld': []}}
    monkeypatch.setattr(m, '_request_json', fake)
    with pytest.raises(RuntimeError, match='no verifiable'):
        m.fetch_news('000157.SZ', as_of='2026-09-18')
    decoded = urllib.parse.unquote(captured['url'])
    assert 'cmsArticleWebOld' in decoded
    assert '"type":[' in decoded or '"type":["cmsArticleWebOld"]' in decoded


def test_news_drops_unowned_rows(monkeypatch):
    payload = {'hitsTotal': 3, 'result': {'cmsArticleWebOld': [
        {'date': '2026-09-10 19:56:00', 'title': '其它公司行业新闻',
         'content': '纯行业新闻正文，与本公司无关', 'code': '1'},
        {'date': '2026-09-09 10:00:00', 'title': '本公司新闻标题',
         'content': '详情000157内容', 'code': '2'},
    ]}}
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_news('000157.SZ', as_of='2026-09-18')
    assert result['coverage_count'] == 1


def test_announcements_drop_future_dated_rows_but_keep_valid(monkeypatch):
    payload = {'success': 1, 'data': {'list': [
        {'art_code': '1', 'codes': [{'stock_code': '000157'}],
         'title': '未来公告', 'notice_date': '2026-09-19 00:00:00',
         'display_time': '2026-09-19 09:00:00'},
        {'art_code': '2', 'codes': [{'stock_code': '000157'}],
         'title': '历史公告', 'notice_date': '2026-09-15 00:00:00',
         'display_time': '2026-09-15 09:00:00'},
    ]}}
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_announcements('000157.SZ', as_of='2026-09-18')
    assert result['coverage_count'] == 1


def test_ratings_zero_hits_is_a_verified_empty_result(monkeypatch):
    payload = {'hits': 0, 'data': []}
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_ratings('600757.SH', as_of='2026-09-18')
    assert result == {
        'complete': True, 'status': 'ok', 'level': 'none', 'reasons': [],
        'source': 'Eastmoney analyst ratings', 'source_as_of': None,
        'coverage_count': 0, 'freshness': 'no_records',
    }


def test_ratings_empty_result_requires_validated_response(monkeypatch):
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: {'hits': 0, 'data': {}})
    with pytest.raises(ValueError, match='rating response data invalid'):
        m.fetch_ratings('600757.SH', as_of='2026-09-18')


def test_news_accepts_code_in_title_or_body(monkeypatch):
    payload = {'hitsTotal': 1, 'result': {'cmsArticleWebOld': [
        {'date': '2026-09-09 10:00:00', 'title': '行业新闻标题不含代码',
         'content': '但是正文里提到了000157这家公司', 'code': '2'},
    ]}}
    monkeypatch.setattr(m, '_request_json', lambda *a, **kw: payload)
    result = m.fetch_news('000157.SZ', as_of='2026-09-18')
    assert result['coverage_count'] == 1


# --- Live probes: hit the real endpoints for one code and confirm shape ---


@pytest.mark.network
def test_live_probe_announcement_returns_owned_rows():
    m._CIRCUIT_OPEN.clear()
    result = m.fetch_announcements('000157.SZ', as_of='2026-09-18')
    assert result['complete'] is True
    assert result['coverage_count'] >= 1
    assert result['source'] == 'Eastmoney announcements'


@pytest.mark.network
def test_live_probe_ratings_returns_rows():
    m._CIRCUIT_OPEN.clear()
    result = m.fetch_ratings('000157.SZ', as_of='2026-09-18')
    assert result['complete'] is True
    assert result['coverage_count'] >= 1
    assert result['source'] == 'Eastmoney analyst ratings'


@pytest.mark.network
def test_live_probe_news_returns_owned_rows():
    m._CIRCUIT_OPEN.clear()
    result = m.fetch_news('000157.SZ', as_of='2026-09-18')
    assert result['complete'] is True
    assert result['coverage_count'] >= 1
    assert result['source'] == 'Eastmoney news'
