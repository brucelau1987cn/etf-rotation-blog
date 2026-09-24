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
    assert "rollingDailyReports['2026-09-24']" in page
    assert "'2026-09-23': {" in data
    latest = data[data.index("'2026-09-24': {"):data.index("'2026-09-23': {")]
    assert latest.count('{ name:') == 12
    assert latest.count("validation: 'confirmed'") >= 9
    assert latest.count("validation: 'reclaimed'") >= 1
    assert 'SELL' in latest and '德福科技' in latest
    for text in ("创新医疗", "纽约白银期货", "今日操作结论", "什么时候买", "什么时候卖", "今日信号表", "逐标的计划", "执行纪律"):
        assert text in latest + component
    for text in ("海光信息", "中国宏桥"):
        assert text in data, f"{text} missing across all reports"
    assert (ROOT / "src/pages/rolling/insights/2026-09-21.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-21.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-18.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-17.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-16.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-15.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-14.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-11.astro").exists()
    assert "/rolling/insights/2026-09-24 /rolling/insights/2026-09-24/ 301" in redirects
    assert "/rolling/insights/2026-09-23 /rolling/insights/2026-09-23/ 301" in redirects
    assert "/rolling/insights/2026-09-18 /rolling/insights/2026-09-18/ 301" in redirects
    assert "/rolling/insights/2026-09-17 /rolling/insights/2026-09-17/ 301" in redirects
    assert "/rolling/insights/2026-09-16 /rolling/insights/2026-09-16/ 301" in redirects
    assert "/rolling/insights/2026-09-15 /rolling/insights/2026-09-15/ 301" in redirects
    assert "/rolling/insights/2026-09-14 /rolling/insights/2026-09-14/ 301" in redirects
    assert "/rolling/insights/2026-09-11 /rolling/insights/2026-09-11/ 301" in redirects

def test_no_standalone_stock_title():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    assert "<h1>创新医疗" not in page

def test_insights_2026_09_22_archive():
    import re
    data = (ROOT / 'src/data/rolling-daily-insights.ts').read_text(encoding='utf-8')
    assert "'2026-09-22':" in data, 'archive 2026-09-22 entry missing'
    assert "'688041'" in data, '海光信息 symbol missing in archive'
    assert "'002173'" in data, '创新医疗 symbol missing in archive'
    assert "'002185'" in data, '华天科技 symbol missing in archive'
    assert "'01378'" in data, '中国宏桥 symbol missing in archive'
    assert "'SI=F'" in data, 'SI=F missing in archive'
    assert '9月22日滚动信号收盘复盘' in data, 'archive title missing'
    assert data.index("'2026-09-23':") < data.index("'2026-09-22':"), 'today must come before archive'
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
    assert '9月23日滚动信号收盘复盘' in data, 'title missing'
    assert data.index("'2026-09-23':") < data.index("'2026-09-22':"), 'today must come before prev'
    page = (ROOT / 'src/pages/rolling/insights.astro').read_text(encoding='utf-8')
    assert "rollingDailyReports['2026-09-24']" in page, 'insights.astro not pointing to today'
    cat = re.search(r"rollingDailyArticleCatalog = \[(.+?)\];", data, re.S).group(1)
    assert "tradeDate: '2026-09-24'" in cat and "/rolling/insights/'" in cat.split("tradeDate: '2026-09-24'")[1].split('},')[0], 'catalog latest must be today'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-23.astro').exists(), 'today static page missing'
    assert (ROOT / 'src/pages/rolling/insights/2026-09-22.astro').exists(), 'prev static page missing'
    redirects = (ROOT / 'public/_redirects').read_text(encoding='utf-8')
    assert '/rolling/insights/2026-09-23 /rolling/insights/2026-09-23/ 301' in redirects
    assert '/rolling/insights/2026-09-22 /rolling/insights/2026-09-22/ 301' in redirects
    # contract: SI=F BUY 3h + 4 SELLs, 002173 confirmed, 002185/600703 reclaimed, 600021 confirmed
    today_block = data[data.index("'2026-09-23': {"):data.index("'2026-09-22': {")]
    assert today_block.count("validation: 'confirmed'") >= 2
    assert today_block.count("validation: 'reclaimed'") >= 2
    assert 'BUY 3h' in today_block or '3h BUY' in today_block
