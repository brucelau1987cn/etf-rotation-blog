from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_rolling_subnav_has_insights_after_four_markets():
    text = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    labels = ["A股滚动", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    assert [text.index(x) for x in labels] == sorted(text.index(x) for x in labels)
    assert "'/rolling/insights/'" in text

def test_latest_daily_report_contract_and_archive():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    component = (ROOT / "src/components/RollingDailyInsightReport.astro").read_text(encoding="utf-8")
    data = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    redirects = (ROOT / "public/_redirects").read_text(encoding="utf-8")
    assert "rollingDailyReports['2026-09-21']" in page
    assert "'2026-09-18': {" in data
    latest = data[data.index("'2026-09-21': {"):data.index("'2026-09-18': {")]
    assert latest.count('{ name:') == 4
    assert latest.count("validation: 'confirmed'") == 1
    assert latest.count("validation: 'watch'") == 3
    for text in ("创新医疗", "东方明珠", "中国宏桥", "纽约白银期货", "今日操作结论", "什么时候买", "什么时候卖", "今日信号表", "逐标的计划", "执行纪律"):
        assert text in latest + component
    assert (ROOT / "src/pages/rolling/insights/2026-09-18.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-17.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-16.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-15.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-14.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-11.astro").exists()
    assert "/rolling/insights/2026-09-18 /rolling/insights/2026-09-18/ 301" in redirects
    assert "/rolling/insights/2026-09-17 /rolling/insights/2026-09-17/ 301" in redirects
    assert "/rolling/insights/2026-09-16 /rolling/insights/2026-09-16/ 301" in redirects
    assert "/rolling/insights/2026-09-15 /rolling/insights/2026-09-15/ 301" in redirects
    assert "/rolling/insights/2026-09-14 /rolling/insights/2026-09-14/ 301" in redirects
    assert "/rolling/insights/2026-09-11 /rolling/insights/2026-09-11/ 301" in redirects

def test_no_standalone_stock_title():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    assert "<h1>创新医疗" not in page

def test_insights_2026_09_21():
    import re
    p = ROOT / 'src/data/rolling-daily-insights.ts'
    data = p.read_text(encoding='utf-8')
    assert "'2026-09-21':" in data, 'today report key missing'
    assert "'600637'" in data, '东方明珠 symbol missing'
    assert "'002173'" in data, '创新医疗 symbol missing'
    assert "'01378'" in data, '中国宏桥 symbol missing'
    assert "'SI=F'" in data, 'SI=F missing'
    _ty='2026'; _tm=9; _td=21
    assert '9月21日滚动信号收盘复盘' in data, 'title missing'
    assert 'BUY' in data, 'BUY direction missing'
    assert 'SELL' in data, 'SELL direction missing'
    assert data.index("'2026-09-21':") < data.index("'2026-09-18':"), 'today must come before prev'
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-09-21']" in page, 'insights.astro not pointing to today'
    cat = re.search(r"rollingDailyArticleCatalog = \[(.+?)\];", data, re.S).group(1)
    assert "'2026-09-21', href: '/rolling/insights/'" in cat, 'catalog latest must be today at /rolling/insights/'
    assert "'2026-09-18', href: '/rolling/insights/2026-09-18/'" in cat, 'catalog demote entry missing'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-18.astro').exists(), 'prev static page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert "/rolling/insights/2026-09-18 /rolling/insights/2026-09-18/ 301" in redirects, 'prev canonicalising redirect missing'
