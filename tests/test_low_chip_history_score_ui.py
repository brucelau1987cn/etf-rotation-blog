"""Offline browser tests for the real inline low-chip history controller."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'src/pages/rolling/low-chip.astro'
DATES = ['2026-10-03', '2026-10-02', '2026-10-01']


@pytest.fixture
def ui():
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:
        pytest.skip('Chromium is required for the offline UI regression')
    source = PAGE.read_text(encoding='utf-8')
    script = source.split('<script is:inline define:vars=', 1)[1].split('>\n', 1)[1].split('</script>', 1)[0]
    shell = '''<div id="chip-list"><div class="chip-row" data-symbol="LATEST" data-name="最新" data-initials="" data-industry="" data-composite="80"><span class="chip-reco is-高" title="推荐度：高（80/100）">高</span></div></div>
    <div id="chip-pager" data-page-size="6"><div id="chip-page-nums"></div><span id="chip-page-meta"></span><button id="chip-page-prev"></button><button id="chip-page-next"></button></div>
    <input id="chip-search-input"><div id="chip-search-suggestions" hidden></div><div id="chip-search-empty" hidden></div>
    <button id="chip-history-prev"></button><button id="chip-history-next"></button><span id="chip-history-status"></span>'''
    initial = {'data_as_of': DATES[0], 'rows': [{'symbol': 'LATEST', 'name': '最新', 'financials': {}, 'shareholderMetrics': {}, 'profits': {'week': 0, 'month': 0, 'quarter': 0}, 'composite': 80, 'change_percent': 0}]}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        context.route('**/*', lambda route: route.abort())
        page = context.new_page()
        page.set_content(shell)
        setup = '''window.requests = []; window.responses = {};
          window.fetch = function(url) { window.requests.push(url); const day = url.split('date=')[1];
            return Promise.resolve({ok: true, json: () => Promise.resolve(window.responses[day])}); };
        '''
        setup += 'var initialData=' + json.dumps(initial, ensure_ascii=False) + ';'
        setup += 'var historyIndex=' + json.dumps({'dates': DATES, 'items': []}) + ';'
        setup += 'var occurrenceLookup={}; var stockInitialsByCode={}; var pageSize=6;'
        page.add_script_tag(content=setup + script)
        yield page
        context.close()
        browser.close()


def test_history_missing_cycles_has_no_fabricated_recommendation(ui):
    ui.evaluate("responses['2026-10-02'] = {results: [{stock_code:'600001', stock_name:'历史'}]}")
    ui.locator('#chip-history-prev').click()
    row = ui.locator('.chip-row[data-symbol="600001"]')
    assert row.locator('.chip-reco').inner_text() == '暂不可用'
    assert row.get_attribute('data-composite') is None
    assert '/100' not in row.locator('.chip-reco').get_attribute('title')


def test_history_cycle_validation_order_and_privacy(ui):
    records = [
        {'stock_code': 'missing', 'stock_name': '缺字段'},
        {'stock_code': 'null', 'stock_name': '空值', 'week_profit': None, 'month_profit': 0, 'quarter_profit': 0},
        {'stock_code': 'blank', 'stock_name': '空串', 'week_profit': ' ', 'month_profit': 0, 'quarter_profit': 0},
        {'stock_code': 'bad', 'stock_name': '异常', 'week_profit': 'NaN', 'month_profit': 0, 'quarter_profit': 0},
        {'stock_code': 'partial', 'stock_name': '部分', 'week_profit': 0, 'month_profit': '0'},
        {'stock_code': 'zero', 'stock_name': '零值', 'week_profit': 0, 'month_profit': 0, 'quarter_profit': 0},
        {'stock_code': 'string', 'stock_name': '数字串', 'week_profit': '0', 'month_profit': '0', 'quarter_profit': '0'},
    ]
    ui.evaluate('rows => { responses["2026-10-02"] = {results: rows}; }', records)
    ui.locator('#chip-history-prev').click()
    rows = ui.locator('.chip-row[data-symbol]')
    assert rows.evaluate_all('(els) => els.map(e => e.dataset.symbol)') == [r['stock_code'] for r in records]
    for code in ['missing', 'null', 'blank', 'bad', 'partial']:
        row = ui.locator(f'[data-symbol="{code}"]')
        assert row.locator('.chip-reco').inner_text() == '暂不可用'
        assert row.get_attribute('data-composite') is None
    for code in ['zero', 'string']:
        row = ui.locator(f'[data-symbol="{code}"]')
        assert row.get_attribute('data-composite') is not None
        assert '/100' in row.locator('.chip-reco').get_attribute('title')
    assert ui.locator('[data-symbol="zero"]').get_attribute('data-composite') == ui.locator('[data-symbol="string"]').get_attribute('data-composite')
    for row in rows.all():
        for key in ['data-week-profit', 'data-month-profit', 'data-quarter-profit']:
            assert row.get_attribute(key) is None


def test_history_switch_cache_search_and_latest_repaint(ui):
    ui.evaluate('''() => {
      responses['2026-10-02'] = {results: [{stock_code:'B', stock_name:'历史B'}, {stock_code:'A', stock_name:'历史A'}]};
      responses['2026-10-01'] = {results: [{stock_code:'C', stock_name:'历史C'}]};
    }''')
    ui.locator('#chip-history-prev').click()
    ui.locator('#chip-search-input').fill('A')
    assert ui.locator('[data-symbol="A"]').is_visible()
    assert ui.locator('[data-symbol="B"]').is_hidden()
    ui.locator('#chip-history-prev').click()
    assert ui.locator('[data-symbol="C"]').is_hidden()
    ui.locator('#chip-history-next').click()
    assert ui.locator('[data-symbol="A"]').is_visible()
    assert ui.locator('[data-symbol="A"] .chip-reco').inner_text() == '暂不可用'
    ui.locator('#chip-search-input').fill('')
    assert ui.locator('.chip-row[data-symbol]').evaluate_all('(els) => els.map(e => e.dataset.symbol)') == ['B', 'A']
    ui.locator('#chip-history-next').click()
    latest = ui.locator('[data-symbol="LATEST"]')
    assert latest.get_attribute('data-composite') == '80'
    assert latest.locator('.chip-reco').inner_text() == '高'
    assert len(ui.evaluate('requests')) == 2


@pytest.mark.parametrize('field', ['week_profit', 'month_profit', 'quarter_profit'])
@pytest.mark.parametrize('value', ['0x0', -1, 100.01, '101', 'NaN', 'Infinity', True, ' '])
def test_invalid_cycle_values_are_unavailable(ui, field, value):
    record = {'stock_code': 'BAD', 'stock_name': '异常', 'week_profit': 0, 'month_profit': 0, 'quarter_profit': 0}
    record[field] = value
    ui.evaluate('(record) => { responses["2026-10-02"] = {results: [record]}; }', record)
    ui.locator('#chip-history-prev').click()
    row = ui.locator('[data-symbol="BAD"]')
    assert row.locator('.chip-reco').inner_text() == '暂不可用'
    assert row.get_attribute('data-composite') is None


def projected_response(rows, day):
    """Call the actual authenticated API handler with a local D1 double."""
    js = r"""
      import { onRequest } from './functions/api/public/v1/low-chip-metrics.js';
      import { signToken } from './functions/_lib/subscription-auth.js';
      const {rows, day} = JSON.parse(process.argv[1]);
      const token = await signToken({role:'admin', sub:'admin:2', exp:'2099-01-01T00:00:00Z'}, 'test-secret');
      const calls = [];
      const DB = {prepare(sql) {return {bind(...args) {calls.push({sql,args}); return this},
        async run() {return {}}, async all() {return {results:rows}}};}};
      const response = await onRequest({
        request: new Request('https://example.test/api/public/v1/low-chip-metrics?date=' + day,
          {headers:{Cookie:'etf_admin=' + token}}), env:{DB, ADMIN_SECRET:'test-secret'}
      });
      if (response.status !== 200) throw new Error('HTTP ' + response.status);
      if (!calls.some(c => c.sql.includes('week_profit IS NOT NULL') &&
          c.sql.includes('month_profit IS NOT NULL') && c.sql.includes('quarter_profit IS NOT NULL')))
        throw new Error('missing membership query');
      console.log(JSON.stringify(await response.json()));
    """
    result = subprocess.run(['node', '--input-type=module', '-e', js, json.dumps({'rows': rows, 'day': day})],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_real_api_projection_pagination_search_and_cache(ui):
    raw = [{'trade_date':'20261002', 'stock_code':f'{600000+i}', 'stock_name':f'历史{i:02}',
            'week_profit': i, 'month_profit': 0, 'quarter_profit': 1, 'main_force':40-i,
            'financials':'{"roe":10}', 'industry_etfs':'[{"code":"512480.SH"}]',
            'shareholder_nature':'{"quality_shareholder":true}', 'theme_concepts':'["半导体"]'}
           for i in range(14)]
    projected = projected_response(raw, DATES[1])
    second = projected_response([dict(raw[0], trade_date='20261001', stock_code='700000')], DATES[2])
    assert projected['count'] == 14
    assert projected['results'][0]['industry_etfs'] == [{'code':'512480.SH'}]
    assert projected['results'][0]['shareholder_nature']['quality_shareholder'] is True
    for row in projected['results']:
        assert all(field not in row for field in ('week_profit', 'month_profit', 'quarter_profit'))
    ui.evaluate('(payloads) => { responses["2026-10-02"] = payloads[0]; responses["2026-10-01"] = payloads[1]; }', [projected, second])
    ui.locator('#chip-history-prev').click()
    rows = ui.locator('.chip-row[data-symbol]')
    assert rows.evaluate_all('(els) => els.map(e => e.dataset.symbol)') == [r['stock_code'] for r in raw]
    def visible():
        return rows.evaluate_all('(els) => els.filter(e => !e.hidden).map(e => e.dataset.symbol)')
    assert visible() == [r['stock_code'] for r in raw[:6]]
    assert ui.locator('#chip-page-meta').inner_text() == '第 1/3 页 · 14 只'
    ui.locator('#chip-page-next').click()
    assert visible() == [r['stock_code'] for r in raw[6:12]]
    ui.locator('#chip-page-nums button').filter(has_text='3').click()
    assert visible() == [r['stock_code'] for r in raw[12:]]
    ui.locator('#chip-page-prev').click()
    assert visible() == [r['stock_code'] for r in raw[6:12]]
    ui.locator('#chip-search-input').fill('600013')
    assert visible() == ['600013']
    assert ui.locator('#chip-pager').is_hidden()
    ui.locator('#chip-history-prev').click()
    assert ui.locator('[data-symbol="700000"]').is_hidden()
    ui.locator('#chip-history-next').click()
    assert visible() == ['600013']
    ui.locator('#chip-search-input').fill('')
    assert visible() == [r['stock_code'] for r in raw[:6]]
    ui.locator('#chip-page-nums button').filter(has_text='3').click()
    assert visible() == [r['stock_code'] for r in raw[12:]]
    for row in rows.all():
        assert row.locator('.chip-reco').inner_text() == '暂不可用'
        assert row.get_attribute('data-composite') is None
        for key in ('data-week-profit', 'data-month-profit', 'data-quarter-profit'):
            assert row.get_attribute(key) is None
    assert ui.evaluate('requests') == [f'/api/public/v1/low-chip-metrics?date={d}' for d in DATES[1:]]


@pytest.mark.parametrize('field', ['week_profit', 'month_profit', 'quarter_profit'])
@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_number_in_any_cycle_is_unavailable(ui, field, value):
    ui.evaluate('([field, value]) => { const row = {stock_code:"BAD", week_profit:0, month_profit:0, quarter_profit:0}; row[field] = value === "NaN" ? NaN : value === "Infinity" ? Infinity : -Infinity; responses["2026-10-02"] = {results:[row]}; }', [field, value])
    ui.locator('#chip-history-prev').click()
    row = ui.locator('[data-symbol="BAD"]')
    assert row.locator('.chip-reco').inner_text() == '暂不可用'
    assert row.get_attribute('data-composite') is None


@pytest.mark.parametrize('field', ['week_profit', 'month_profit', 'quarter_profit'])
@pytest.mark.parametrize('value', [0, 100, '0', '100', ' 1.25 ', '.5', '1e1'])
def test_valid_cycle_boundaries_and_numeric_strings_are_available(ui, field, value):
    row = {'stock_code':'OK', 'week_profit':0, 'month_profit':0, 'quarter_profit':0}
    row[field] = value
    ui.evaluate('(row) => { responses["2026-10-02"] = {results:[row]}; }', row)
    ui.locator('#chip-history-prev').click()
    badge = ui.locator('[data-symbol="OK"] .chip-reco')
    assert badge.inner_text() in ('高', '中', '低')
    assert '/100' in badge.get_attribute('title')


def test_latest_real_ssr_scoring_sort_and_repaint_matches_baseline():
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:
        pytest.skip('Chromium required')
    baseline = ROOT / 'tests/fixtures/low_chip_pre_history_score_patch'
    original_script = (baseline / 'controller.js').read_text(encoding='utf-8')
    scorer = (baseline / 'ssr-score.ts').read_text(encoding='utf-8')
    modified = PAGE.read_text(encoding='utf-8')
    assert 'const chipFocusScore =' + modified.split('const chipFocusScore =', 1)[1].split('// 推荐度标签', 1)[0] == scorer
    modified_script = modified.split('<script is:inline define:vars=', 1)[1].split('>\n', 1)[1].split('</script>', 1)[0]
    strip = subprocess.run(['node', '--input-type=module', '-e',
        "import {stripTypeScriptTypes} from 'node:module'; console.log(stripTypeScriptTypes(process.argv[1]));", scorer],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    latest = [
        {'symbol':'LOW', 'name':'低', 'financials':{'roe':-1,'net_margin':-1,'debt_ratio':80},
         'shareholderMetrics':{'main_force':10,'shareholder_change_pct':10}, 'profits':{'week':2,'month':2,'quarter':2}},
        {'symbol':'HIGH', 'name':'高', 'financials':{'roe':10,'net_margin':20,'debt_ratio':20},
         'shareholderMetrics':{'main_force':40,'shareholder_change_pct':-15,'chip_focus':'非常集中'},
         'profits':{'week':0,'month':0,'quarter':0}},
        {'symbol':'MID', 'name':'中', 'financials':{'roe':5,'net_margin':7,'debt_ratio':40},
         'shareholderMetrics':{'main_force':25,'shareholder_change_pct':-6}, 'profits':{'week':1,'month':1,'quarter':1}},
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        results = []
        for script in (original_script, modified_script):
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content('<div id="chip-list"></div><div id="chip-pager" data-page-size="6"><div id="chip-page-nums"></div><span id="chip-page-meta"></span><button id="chip-page-prev"></button><button id="chip-page-next"></button></div><input id="chip-search-input"><button id="chip-history-prev"></button><button id="chip-history-next"></button><span id="chip-history-status"></span>')
            scored = page.evaluate('([rows, scorer]) => { const profitBySymbol = Object.fromEntries(rows.map(r => [r.symbol, r.profits])); return Function("profitBySymbol", "rows", scorer + `\nreturn rows.map(r => ({...r, composite:compositeScore(r.symbol,{financials:r.financials,shareholder_metrics:r.shareholderMetrics})})).sort((a,b)=>b.composite-a.composite);`)(profitBySymbol, rows); }', [latest, strip])
            assert [row['symbol'] for row in scored] == ['HIGH','MID','LOW']
            assert [row['composite'] for row in scored] == [100, 62, 20]
            page.evaluate('rows => { document.querySelector("#chip-list").innerHTML = rows.map(r => `<div class="chip-row" data-symbol="${r.symbol}" data-name="${r.name}" data-composite="${r.composite}"><span class="chip-reco" title="推荐度：${r.composite >= 70 ? "高" : r.composite >= 55 ? "中" : "低"}（${r.composite}/100）">${r.composite >= 70 ? "高" : r.composite >= 55 ? "中" : "低"}</span></div>`).join(""); }', scored)
            setup = 'var initialData=' + json.dumps({'data_as_of':DATES[0], 'rows':scored}, ensure_ascii=False) + ';'
            setup += 'var historyIndex=' + json.dumps({'dates':DATES, 'items':[]}) + ';var occurrenceLookup={};var stockInitialsByCode={};var pageSize=6;'
            setup += "window.fetch=()=>Promise.resolve({ok:true,json:()=>Promise.resolve({results:[{stock_code:'600000'}]})});"
            page.add_script_tag(content=setup + script)
            def snapshot():
                return page.locator('.chip-row[data-symbol]').evaluate_all('(els) => els.map(e => [e.dataset.symbol,e.dataset.composite,e.querySelector(".chip-reco").textContent,e.querySelector(".chip-reco").title])')
            before = snapshot()
            page.locator('#chip-history-prev').click()
            page.locator('[data-symbol="600000"]').wait_for()
            page.locator('#chip-history-next').click()
            after = snapshot()
            assert after == before
            results.append(after)
            page.close()
        browser.close()
    assert results[1] == results[0]
