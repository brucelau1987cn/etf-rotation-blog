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
    assert "rollingDailyReports['2026-09-17']" in page
    assert "'2026-09-17': {" in data
    latest = data[data.index("'2026-09-17': {"):data.index("'2026-09-16': {")]
    assert latest.count('{"name":') == 8
    assert latest.count('"validation": "confirmed"') == 4
    assert latest.count('"validation": "reclaimed"') == 1
    assert latest.count('"validation": "watch"') == 3
    for text in ("上海电力", "德福科技", "长鑫科技", "国民技术", "中国宏桥", "澜起科技", "纽约白银", "特斯拉", "今日操作结论", "什么时候买", "什么时候卖", "今日信号表", "逐标的计划", "执行纪律"):
        assert text in latest + component
    assert (ROOT / "src/pages/rolling/insights/2026-09-16.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-16.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-14.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-09-11.astro").exists()
    assert "/rolling/insights/2026-09-16 /rolling/insights/2026-09-16/ 301" in redirects
    assert "/rolling/insights/2026-09-16 /rolling/insights/2026-09-16/ 301" in redirects
    assert "/rolling/insights/2026-09-15 /rolling/insights/2026-09-15/ 301" in redirects
    assert "/rolling/insights/2026-09-14 /rolling/insights/2026-09-14/ 301" in redirects
    assert "/rolling/insights/2026-09-11 /rolling/insights/2026-09-11/ 301" in redirects

def test_no_standalone_stock_title():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    assert "<h1>创新医疗" not in page
