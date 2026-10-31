from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_low_chip_base.py"


@pytest.fixture(autouse=True)
def _no_retry_backoff_sleep(monkeypatch):
    """Retry backoff is real elapsed time; the assertions are not.

    paginate() sleeps FETCH_BACKOFF_S * attempt between integrity retries, and
    every module instance here shares the global `time` module, so patch it once
    for the whole file. Without this the pagination-retry tests add minutes to
    the suite (and to the nightly candidate gate, which runs the full suite).
    """
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_: None)


def load_module():
    spec = importlib.util.spec_from_file_location("build_low_chip_threshold", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_week_month_quarter_queries_use_strict_one_point_five_percent_threshold():
    module = load_module()

    assert [label for label, _, _ in module.PERIODS] == ["week", "month", "quarter"]
    assert all("收盘获利小于1.5%" in query for _, _, query in module.PERIODS)
    assert all("不超过1.5%" not in query for _, _, query in module.PERIODS)


def test_published_threshold_is_one_point_five_percent(monkeypatch, tmp_path):
    module = load_module()
    setattr(module, "DATA", tmp_path / "low-chip.json")

    rows_by_prefix = {
        "周线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "周线收盘获利[20260903]": 1.4999},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "周线收盘获利[20260903]": 1.5},
        ],
        "月线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "月线收盘获利[20260903]": 1.2},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "月线收盘获利[20260903]": 1.3},
        ],
        "季线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "季线收盘获利[20260903]": 1.1},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "季线收盘获利[20260903]": 1.4},
        ],
    }

    def fake_paginate(query):
        for prefix, rows in rows_by_prefix.items():
            if prefix in query:
                return rows, len(rows)
        return [], 0

    monkeypatch.setattr(module, "paginate", fake_paginate)
    assert module.main() == 0

    import json

    payload = json.loads(getattr(module, "DATA").read_text(encoding="utf-8"))
    assert payload["threshold"] == 1.5
    assert payload["intersection"] == ["000001.SZ"]
    assert all(item["symbol"] != "000002.SZ" for item in payload["periods"]["week"])


def test_missing_quarter_metric_blocks_instead_of_synthesizing_zero(monkeypatch, tmp_path):
    module = load_module()
    module.DATA = tmp_path / 'snapshot.json'
    def paginate(query):
        prefix = next((p for _, p, _ in module.PERIODS if p in query), '周线收盘获利')
        field = '收盘获利' if prefix == '季线收盘获利' else prefix
        return [{'股票代码': '000001.SZ', '股票简称': '样本', field + '[20261030]': 0.141}], 1
    monkeypatch.setattr(module, 'paginate', paginate)
    with pytest.raises(SystemExit, match='STAGING BLOCKER.*季线收盘获利'):
        module.main()
    assert not module.DATA.exists()


def test_fetch_retries_metric_drift_before_accepting_page(monkeypatch):
    module = load_module()
    attempts = []
    def fake_run(*args, **kwargs):
        import json
        import subprocess
        attempts.append(1)
        field = '收盘获利' if len(attempts) == 1 else '季线收盘获利'
        return subprocess.CompletedProcess([], 0, json.dumps({'datas': [{field + '[20261030]': 0.1}]}), '')
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    monkeypatch.setattr(module.time, 'sleep', lambda *_: None)
    result = module.fetch('A股 季线收盘获利小于1.5%', 1)
    assert len(attempts) == 2
    assert '季线收盘获利[20261030]' in result['datas'][0]


# ============ pagination must never silently truncate ============

def test_paginate_walks_past_ten_pages_while_has_more_is_true(monkeypatch):
    """2026-10-22: a broad selloff pushed week/month past 1000 rows. The old
    `if p > 10: break` returned 1000 rows with no signal, and the published
    pool looked complete while being truncated."""
    module = load_module()
    pages = {n: {"datas": [{"股票代码": f"{n:03d}000.SZ"}], "has_more": n < 12, 'code_count': 12} for n in range(1, 13)}
    seen = []

    def fake_fetch(query, page):
        seen.append(page)
        return pages[page]

    monkeypatch.setattr(module, "fetch", fake_fetch)
    rows, count = module.paginate("q")
    assert seen == list(range(1, 13))
    assert count == 12


def test_paginate_raises_instead_of_returning_a_truncated_pool(monkeypatch):
    """Hitting the runaway cap must fail closed — a truncated screen would
    silently publish a wrong pool."""
    module = load_module()
    monkeypatch.setattr(module, "MAX_PAGES", 3)
    monkeypatch.setattr(module, "fetch", lambda q, p: {"datas": [{'股票代码': f'{p:06d}.SZ'}], "has_more": True, 'code_count': 1000})

    with pytest.raises(SystemExit) as excinfo:
        module.paginate("q")
    assert "truncated pool" in str(excinfo.value)


def test_max_pages_is_far_above_any_real_result_set():
    """The cap only bounds a runaway loop; a real selloff exceeds 1000 rows."""
    module = load_module()
    assert module.MAX_PAGES >= 100


def test_pagination_blocks_early_empty_page_with_declared_total(monkeypatch):
    module = load_module()
    pages = {
        1: {'datas': [{'股票代码': '000001.SZ'}], 'has_more': True, 'code_count': 3},
        2: {'datas': [], 'has_more': False, 'code_count': 3},
    }
    monkeypatch.setattr(module, 'fetch', lambda q, p: pages[p])
    with pytest.raises(SystemExit, match='STAGING BLOCKER.*pagination'):
        module.paginate('q')


@pytest.mark.parametrize('total', [None, 1.9, True, -1, 'bad'])
def test_pagination_requires_nonnegative_integer_total(monkeypatch, total):
    module = load_module()
    monkeypatch.setattr(module, 'fetch', lambda q, p: {
        'datas': [{'股票代码': '000001.SZ'}], 'has_more': False, 'code_count': total,
    })
    with pytest.raises(SystemExit, match='STAGING BLOCKER.*pagination'):
        module.paginate('q')


def test_optional_listing_audit_failure_preserves_core_result(monkeypatch, tmp_path):
    import json
    module = load_module()
    monkeypatch.setattr(module, 'DATA', tmp_path / 'snapshot.json')
    calls = []
    def paginate(query):
        calls.append(query)
        if len(calls) > 3:
            raise SystemExit('STAGING BLOCKER: optional audit metric drift')
        prefix = next(p for _, p, _ in module.PERIODS if p in query)
        return [{'股票代码': '000001.SZ', prefix + '[20261030]': 0.1}], 1
    monkeypatch.setattr(module, 'paginate', paginate)
    assert module.main() == 0
    data = json.loads(getattr(module, 'DATA').read_text())
    assert data['intersection'] == ['000001.SZ']
    assert data['filters']['new_listing_audit']['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('bad_page', [
    {'datas': [], 'has_more': False, 'code_count': 0},
    {'datas': [], 'has_more': False, 'code_count': '3'},
    {'datas': [], 'has_more': False, 'code_count': 3},
    {'datas': [], 'has_more': True, 'code_count': 3},
    {'datas': [{'股票代码': '000002.SZ'}], 'has_more': False, 'code_count': 3},
])
def test_pagination_retries_same_invalid_page_before_accepting(monkeypatch, bad_page):
    module = load_module()
    calls = []
    first = {'datas': [{'股票代码': '000001.SZ'}], 'has_more': True, 'code_count': 3}
    final = {'datas': [{'股票代码': '000002.SZ'}, {'股票代码': '000003.SZ'}],
             'has_more': False, 'code_count': 3}

    def fetch(query, page):
        calls.append(page)
        return first if page == 1 else (bad_page if calls.count(2) < 3 else final)

    monkeypatch.setattr(module, 'fetch', fetch)
    rows, count = module.paginate('q')
    assert calls == [1, 2, 2, 2]
    assert count == 3
    assert [r['股票代码'] for r in rows] == ['000001.SZ', '000002.SZ', '000003.SZ']


@pytest.mark.parametrize('bad_rows', [
    [{'股票代码': '000001.SZ'}],
    [{'股票代码': '000002.SZ'}, {'股票代码': '000002.SZ'}],
    [{}], [None], [{'股票代码': '000002.BAD'}],
])
def test_intermediate_invalid_codes_retry_same_page_without_pollution(monkeypatch, bad_rows):
    module = load_module()
    calls = []
    def fetch(query, page):
        calls.append(page)
        rows = [{'股票代码': f'{page:06d}.SZ'}]
        if page == 2 and calls.count(2) == 1:
            rows = bad_rows
        return {'datas': rows, 'has_more': page < 3, 'code_count': 3}
    monkeypatch.setattr(module, 'fetch', fetch)
    rows, count = module.paginate('q')
    assert calls == [1, 2, 2, 3]
    assert count == 3
    assert [r['股票代码'] for r in rows] == ['000001.SZ', '000002.SZ', '000003.SZ']


@pytest.mark.parametrize('bad_total', [0, 4, None, True])
def test_pagination_integrity_budget_exhaustion_blocks_without_restart(monkeypatch, bad_total):
    module = load_module()
    calls = []

    def fetch(query, page):
        calls.append(page)
        return {'datas': [{'股票代码': f'{page:06d}.SZ'}],
                'has_more': page == 1, 'code_count': 3 if page == 1 else bad_total}

    monkeypatch.setattr(module, 'fetch', fetch)
    with pytest.raises(SystemExit, match='STAGING BLOCKER.*pagination'):
        module.paginate('q')
    assert calls == [1, 2, 2, 2]


@pytest.mark.parametrize('recovers', [True, False])
def test_real_fetch_non_dictionary_row_reaches_same_page_retry(monkeypatch, recovers):
    import json
    import subprocess
    module = load_module()
    calls = []
    def response(args, **kwargs):
        calls.append(args[args.index('--page') + 1])
        rows = [None]
        if recovers and len(calls) == 3:
            rows = [{'股票代码': '000001.SZ', '季线收盘获利[20261030]': 0.1}]
        return subprocess.CompletedProcess(args, 0, json.dumps({
            'datas': rows, 'code_count': 1, 'has_more': False,
        }), '')
    monkeypatch.setattr(module.subprocess, 'run', response)
    monkeypatch.setattr(module.Path, 'write_text', lambda *a, **kw: None)
    monkeypatch.setattr(module.time, 'sleep', lambda *a: None)
    if recovers:
        rows, count = module.paginate('A股 季线收盘获利小于1.5%')
        assert count == 1 and rows[0]['股票代码'] == '000001.SZ'
    else:
        with pytest.raises(SystemExit, match='STAGING BLOCKER.*invalid stock code'):
            module.paginate('A股 季线收盘获利小于1.5%')
    assert calls == ['1', '1', '1']


# ============ transient upstream failures are retried ============

def test_fetch_retries_transient_ssl_failure_then_succeeds(monkeypatch, tmp_path):
    """2026-10-22: iWenCai returned SSL DECRYPTION_FAILED_OR_BAD_RECORD_MAC
    mid-pagination and killed the whole release. Per-page calls are idempotent."""
    module = load_module()
    attempts = []

    class _Result:
        def __init__(self, ok):
            self.returncode = 0 if ok else 1
            self.stdout = '{"datas": [], "has_more": false}' if ok else ""
            self.stderr = "" if ok else '{"error": "[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC]"}'

    def fake_run(*a, **k):
        attempts.append(1)
        return _Result(len(attempts) >= 3)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    assert module.fetch("q", 1) == {"datas": [], "has_more": False}
    assert len(attempts) == 3


@pytest.mark.parametrize("detail", [
    '{"error": "网络错误: TLS/SSL connection has been closed (EOF) (_ssl.c:992)"}',
    '{"error": "发生错误: Remote end closed connection without response"}',
    '{"error": "HTTP 502 Bad Gateway"}',
    '{"error": "connection reset by peer"}',
    '{"error": "read timed out"}',
])
def test_fetch_retries_every_unclassified_transient_variant(monkeypatch, detail):
    """Enumerating TLS failure strings loses — a new variant silently kills the
    release. Only known-terminal errors may skip the retry."""
    module = load_module()
    attempts = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = detail

    def fake_run(*a, **k):
        attempts.append(1)
        return _Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    with pytest.raises(SystemExit):
        module.fetch("q", 1)
    assert len(attempts) == module.FETCH_ATTEMPTS


def test_fetch_does_not_retry_quota_exhaustion(monkeypatch):
    """Quota/auth errors are terminal — retrying them just burns the key pool."""
    module = load_module()
    attempts = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = '{"error": "quota exhausted: code=113"}'

    def fake_run(*a, **k):
        attempts.append(1)
        return _Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    with pytest.raises(SystemExit):
        module.fetch("q", 1)
    assert len(attempts) == 1


def test_fetch_gives_up_after_the_attempt_budget(monkeypatch):
    module = load_module()
    attempts = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = '{"error": "timed out"}'

    def fake_run(*a, **k):
        attempts.append(1)
        return _Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    with pytest.raises(SystemExit):
        module.fetch("q", 1)
    assert len(attempts) == module.FETCH_ATTEMPTS
