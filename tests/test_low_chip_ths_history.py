"""同花顺前复权日线作为腾讯被 WAF 拦截时的第一顺位备源。

背景：2026-10-19 腾讯 fqkline 被腾讯云 WAF 拦截返回 501，
BaoStock 查询接口整体挂死，低筹码风险预审 153 只股票全部走
stock-api CLI 兜底，单只 2.5s+ 超过 360s 硬超时，16:00 链路崩溃。

同花顺 d.10jqka.com.cn 的 01 档是前复权、全市场覆盖、单请求 0.03s。
新浪虽也快，但返回不复权价（600519 实测与 qfq 最大偏差 134 元），
口径不同不能混入 qfq 链路。
"""
import json
import math

from scripts import attach_low_chip_touchstone as module


def _ths_payload(pairs):
    """构造同花顺 JSONP 响应体：日期,开,高,低,收,量,额,换手,..."""
    body = ';'.join(
        f'{day.replace("-", "")},1.0,1.0,1.0,{close},100,100,1.0,,,0' for day, close in pairs
    )
    return 'quotebridge_v6_line_hs_000001_01_2026(' + json.dumps(
        {'rt': '1', 'num': len(pairs), 'total': len(pairs), 'data': body}
    ) + ')'


def test_ths_history_parses_qfq_close_and_normalizes_date(monkeypatch):
    payload = _ths_payload([('2026-10-15', 3.51), ('2026-10-16', 3.52)])

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload.encode('gbk')

    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: _Resp())
    rows = module.fetch_ths_history({'code': '000001', 'market': 'XSHE'}, 640, '2026-10-16')
    assert rows == [
        {'trade_date': '2026-10-15', 'close': 3.51},
        {'trade_date': '2026-10-16', 'close': 3.52},
    ]


def test_ths_history_drops_invalid_close_and_keeps_ascending_order(monkeypatch):
    payload = _ths_payload([
        ('2026-10-14', 3.65), ('2026-10-15', 3.51), ('2026-10-16', 3.52),
    ])
    payload = payload.replace(',3.51,', ',nan,')

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload.encode('gbk')

    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: _Resp())
    rows = module.fetch_ths_history({'code': '000001', 'market': 'XSHE'}, 640, '2026-10-16')
    assert rows == [
        {'trade_date': '2026-10-14', 'close': 3.65},
        {'trade_date': '2026-10-16', 'close': 3.52},
    ]


def test_ths_history_returns_empty_when_source_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise OSError('connection reset')

    monkeypatch.setattr(module.urllib.request, 'urlopen', _boom)
    assert module.fetch_ths_history({'code': '000001', 'market': 'XSHE'}, 640, '2026-10-16') == []


def test_ths_history_retries_year_endpoint_after_502(monkeypatch):
    """年份端点偶发 502；重试一次即可拿到数据，不能让该股整体掉成 0 根。"""
    payload = _ths_payload([('2026-10-15', 3.51), ('2026-10-16', 3.52)])
    calls = {'n': 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload.encode('gbk')

    def _urlopen(req, timeout=None):
        calls['n'] += 1
        url = req.full_url
        # 首次访问 2026 年份端点失败，重试成功；其余年份端点直接失败
        if url.endswith('/2026.js'):
            if calls['n'] == 1:
                raise OSError('HTTP Error 502: Bad Gateway')
            return _Resp()
        raise OSError('HTTP Error 502: Bad Gateway')

    monkeypatch.setattr(module.urllib.request, 'urlopen', _urlopen)
    rows = module.fetch_ths_history({'code': '000001', 'market': 'XSHE'}, 640, '2026-10-16')
    assert rows == [
        {'trade_date': '2026-10-15', 'close': 3.51},
        {'trade_date': '2026-10-16', 'close': 3.52},
    ]


def test_ths_history_falls_back_to_last_js_when_years_unavailable(monkeypatch):
    """年份端点全部 502 时用 /last.js 兜底，保证不是 0 根。"""
    payload = _ths_payload([('2026-10-15', 3.51), ('2026-10-16', 3.52)])

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload.encode('gbk')

    def _urlopen(req, timeout=None):
        if req.full_url.endswith('/last.js'):
            return _Resp()
        raise OSError('HTTP Error 502: Bad Gateway')

    monkeypatch.setattr(module.urllib.request, 'urlopen', _urlopen)
    rows = module.fetch_ths_history({'code': '688087', 'market': 'XSHG'}, 640, '2026-10-16')
    assert rows == [
        {'trade_date': '2026-10-15', 'close': 3.51},
        {'trade_date': '2026-10-16', 'close': 3.52},
    ]


def test_load_history_uses_ths_before_baostock(tmp_path, monkeypatch):
    """腾讯失败后必须走同花顺，不能直接掉到挂死的 BaoStock。"""
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: (_ for _ in ()).throw(OSError('HTTP 501')))
    monkeypatch.setattr(module, 'fetch_ths_history', lambda *a, **k: [
        {'trade_date': '2025-01-08', 'close': 8},
        {'trade_date': '2025-01-09', 'close': 9},
    ])
    monkeypatch.setattr(module, 'fetch_cf_baostock_history', lambda *_: (_ for _ in ()).throw(AssertionError('CF BaoStock used')))
    monkeypatch.setattr(module, 'fetch_baostock_history', lambda *_: (_ for _ in ()).throw(AssertionError('local BaoStock used')))

    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [
        {'trade_date': '2025-01-08', 'close': 8.0},
        {'trade_date': '2025-01-09', 'close': 9.0},
    ]


def test_load_history_falls_through_ths_to_cf_baostock(tmp_path, monkeypatch):
    """同花顺也空时继续降级，保持既有备源顺序不被破坏。"""
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [])
    monkeypatch.setattr(module, 'fetch_ths_history', lambda *a, **k: [])
    monkeypatch.setattr(module, 'fetch_cf_baostock_history', lambda *_: [{'date': '2025-01-09', 'close': 9}])

    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]


def test_ths_rows_are_still_clipped_by_end_date(tmp_path, monkeypatch):
    """同花顺返回超出 end 的 bar 时，必须被 no-lookahead 契约裁掉。"""
    monkeypatch.setattr(module, 'fetch_tencent_history', lambda *_: [])
    monkeypatch.setattr(module, 'fetch_ths_history', lambda *a, **k: [
        {'trade_date': '2025-01-09', 'close': 9},
        {'trade_date': '2025-01-11', 'close': 11},
    ])

    result = module.load_history({'code': '000001', 'market': 'XSHE'}, '2025-01-09', db_path=tmp_path / 'missing.db')
    assert result == [{'trade_date': '2025-01-09', 'close': 9.0}]
