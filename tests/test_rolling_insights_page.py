from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_rolling_subnav_has_insights_after_four_markets():
    text = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    labels = ["A股滚动", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    assert [text.index(x) for x in labels] == sorted(text.index(x) for x in labels)
    assert "'/rolling/insights/'" in text

def test_latest_daily_report_contract_and_archive():
    import json
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    data = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    component = (ROOT / "src/components/RollingDailyInsightReport.astro").read_text(encoding="utf-8")
    redirects = (ROOT / "public/_redirects").read_text(encoding="utf-8")
    assert "rollingDailyReports['2026-10-14']" in page
    block = data.split("'2026-10-14': ",1)[1].split(",\n  '2026-10-13':",1)[0]
    report = json.loads(block)
    assert len(report['signals']) == 3
    assert {k:sum(s['validation']==k for s in report['signals']) for k in ('confirmed','reclaimed','mixed','watch')} == {'confirmed':0,'reclaimed':0,'mixed':0,'watch':3}
    assert block.count('event_id=') == 4
    for name in ('三安光电','华天科技','中国宏桥'):
        assert name in block
    for word in ('UNAVAILABLE','INSUFFICIENT'):
        assert word in block
    assert (ROOT / 'src/pages/rolling/insights/2026-10-13.astro').exists()
    assert (ROOT / 'src/pages/rolling/insights/2026-10-12.astro').exists()
    assert '/rolling/insights/2026-10-12 /rolling/insights/2026-10-12/ 301' in redirects
    assert '/rolling/insights/2026-10-12/ /rolling/insights/ 301' not in redirects
    assert '/rolling/insights/2026-10-13 /rolling/insights/2026-10-13/ 301' in redirects
    assert '/rolling/insights/2026-10-13/ /rolling/insights/ 301' not in redirects
    assert '/rolling/insights/2026-10-14 /rolling/insights/2026-10-14/ 301' in redirects
    assert '/rolling/insights/2026-10-14/ /rolling/insights/ 301' not in redirects
    for label in ('今日操作结论','什么时候买','什么时候卖','今日信号表','逐标的计划','执行纪律'):
        assert label in component


def test_no_standalone_stock_title():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    assert "<h1>创新医疗" not in page

def test_insights_2026_09_22_archive():
    import re
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-09-29':" in data, 'latest entry missing'
    assert "'2026-09-22':" in data, 'archive 2026-09-22 entry missing'
    assert "'688041'" in data, '海光信息 symbol missing in archive'
    assert "'002173'" in data, '创新医疗 symbol missing in archive'
    assert "'002185'" in data, '华天科技 symbol missing in archive'
    assert "'01378'" in data, '中国宏桥 symbol missing in archive'
    assert "'SI=F'" in data, 'SI=F missing in archive'
    assert '9月22日滚动信号收盘复盘' in data, 'archive title missing'
    assert data.index("'2026-09-28':") < data.index("'2026-09-24':"), 'today must come before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-22.astro').exists(), 'archive static page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-22 /rolling/insights/2026-09-22/ 301' in redirects


def test_insights_2026_09_23():
    import re
    p = ROOT / 'src/data/rolling-daily-insights.ts'
    data = p.read_text(encoding='utf-8')
    assert "'2026-09-23':" in data, 'today report key missing'
    assert "'002185'" in data, '华天科技 symbol missing'
    assert "'002173'" in data, '创新医疗 symbol missing'
    assert "'600021'" in data, '上海电力 symbol missing'
    assert "'600703'" in data, '三安光电 symbol missing'
    assert "'SI=F'" in data, 'SI=F missing'
    assert '9月28日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-09-28':") < data.index("'2026-09-24':"), 'today must come before prev'
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-10-14']" in page, 'insights.astro not pointing to latest'
    cat = re.search(r"rollingDailyArticleCatalog = \[(.+?)\];", data, re.S).group(1)
    assert "tradeDate: '2026-10-14'" in cat and "/rolling/insights/'" in cat.split("tradeDate: '2026-10-14'")[1].split('},')[0], 'catalog latest must be today'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-24.astro').exists(), 'archive static page missing'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-22.astro').exists(), 'prev static page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-23 /rolling/insights/2026-09-23/ 301' in redirects
    assert '/rolling/insights/2026-09-22 /rolling/insights/2026-09-22/ 301' in redirects
    # contract: SI=F BUY 3h + 4 SELLs, 002173 confirmed, 002185/600703 reclaimed, 600021 confirmed
    today_block = data[data.index("'2026-09-28': {"):data.index("'2026-09-24': {")]
    assert today_block.count("validation: 'confirmed'") >= 7
    assert today_block.count("validation: 'mixed'") >= 3
    assert 'SI=F' in today_block and '德福科技' in today_block


def test_insights_2026_09_28():
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-09-28': {" in data, 'today report key missing'
    for code in ("'301511'", "'301362'", "'600703'", "'000021'", "'688825'", "'300077'", "'002185'", "'688041'", "'002173'", "'600637'", "'06809'", "'SI=F'"):
        assert code in data, f'{code} missing'
    assert '9月28日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-09-28':") < data.index("'2026-09-24':"), 'today before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-24.astro').exists(), 'archive page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-24 /rolling/insights/2026-09-24/ 301' in redirects
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-10-14']" in page


def test_insights_2026_09_29():
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-09-29': {" in data, 'today report key missing'
    for code in ("'301362'", "'TSLA'", "'SI=F'"):
        assert code in data, f'{code} missing'
    assert '9月29日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-09-29':") < data.index("'2026-09-28':"), 'today before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-29.astro').exists(), 'archive page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-29 /rolling/insights/2026-09-29/ 301' in redirects
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-10-14']" in page


def test_insights_2026_09_30():
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-09-30': {" in data, 'today report key missing'
    for code in ("'600021'", "'002185'", "'688041'", "'301362'", "'06809'", "'SI=F'"):
        assert code in data, f'{code} missing'
    assert '9月30日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-09-30':") < data.index("'2026-09-29':"), 'today before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-29.astro').exists(), 'archive page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-29 /rolling/insights/2026-09-29/ 301' in redirects
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-10-14']" in page
    import re as _re
    cat = _re.search(r"rollingDailyArticleCatalog = \[(.+?)\];", data, _re.S).group(1)
    assert "tradeDate: '2026-10-14'" in cat, 'catalog latest must be today'


def test_insights_2026_10_08():
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-10-08': {" in data, 'today report key missing'
    for code in ("'688825'", "'300077'", "'301511'", "'600637'", "'002185'", "'301362'", "'01378'", "'06809'", "'SI=F'"):
        assert code in data, f'{code} missing'
    assert '10月8日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-10-08':") < data.index("'2026-09-30':"), 'today before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-30.astro').exists(), 'archive page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-30 /rolling/insights/2026-09-30/ 301' in redirects
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-10-14']" in page

def test_insights_2026_10_14():
    import json, re
    data = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    redirects = (ROOT / "public/_redirects").read_text(encoding="utf-8")
    assert "rollingDailyReports['2026-10-14']" in page, 'insights.astro not pointing to latest 2026-10-14'
    assert "'2026-10-14':" in data, 'today report key missing'
    assert "10月14日滚动信号收盘复盘" in data, 'title missing'
    block = data.split("'2026-10-14': ", 1)[1].split(",\n  '2026-10-13':", 1)[0]
    report = json.loads(block)
    assert {s['symbol'] for s in report['signals']} == {'600703', '002185', '01378'}
    assert {k: sum(s['validation'] == k for s in report['signals']) for k in ('confirmed', 'reclaimed', 'mixed', 'watch')} == {'confirmed': 0, 'reclaimed': 0, 'mixed': 0, 'watch': 3}
    assert block.count('event_id=') == 4
    for name in ('三安光电', '华天科技', '中国宏桥'):
        assert name in block, f'{name} missing'
    assert 'UNAVAILABLE' in block, 'UNAVAILABLE marker missing'
    assert 'INSUFFICIENT' in block, 'INSUFFICIENT marker missing'
    assert data.index("'2026-10-14':") < data.index("'2026-10-13':"), 'today must come before archive'
    assert (ROOT / 'src/pages/rolling/insights/2026-10-13.astro').exists(), 'archive 2026-10-13 page missing'
    assert (ROOT / 'src/pages/rolling/insights/2026-10-12.astro').exists(), 'archive 2026-10-12 page missing'
    cat = re.search(r"rollingDailyArticleCatalog = \[(.+?)\];", data, re.S).group(1)
    assert "tradeDate: '2026-10-14'" in cat and "/rolling/insights/'" in cat.split("tradeDate: '2026-10-14'")[1].split('},')[0], 'catalog latest must be 2026-10-14'
    assert "tradeDate: '2026-10-13'" in cat and "/rolling/insights/2026-10-13/'" in cat.split("tradeDate: '2026-10-13'")[1].split('},')[0], 'catalog must list 2026-10-13 archive'
    assert '/rolling/insights/2026-10-13 /rolling/insights/2026-10-13/ 301' in redirects, '2026-10-13 redirect missing'
    assert '/rolling/insights/2026-10-13/ /rolling/insights/ 301' not in redirects, 'archive must not redirect to root'
    assert '/rolling/insights/2026-10-14 /rolling/insights/2026-10-14/ 301' in redirects, '2026-10-14 redirect missing'

