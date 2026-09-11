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
    assert "rollingDailyReports['2026-09-11']" in page
    assert "'2026-09-11': {" in data
    latest = data[data.index("'2026-09-11': {"):data.index("'2026-09-10': {")]
    assert latest.count("name: '") == 14
    assert latest.count("validation: 'confirmed'") == 2
    assert latest.count("validation: 'reclaimed'") == 10
    assert latest.count("validation: 'watch'") == 2
    for text in ("上海电力", "东方明珠", "三安光电", "深科技", "德福科技", "民爆光电", "海光信息", "长鑫科技", "国民技术", "华天科技", "创新医疗", "澜起科技", "中国宏桥", "白银期货", "今日操作结论", "什么时候买", "什么时候卖", "今日信号表", "逐标的计划", "执行纪律"):
        assert text in latest + component
    assert (ROOT / "src/pages/rolling/insights/2026-09-10.astro").exists()
    assert "/rolling/insights/2026-09-10 /rolling/insights/2026-09-10/ 301" in redirects
    assert "/rolling/insights/2026-09-09 /rolling/insights/2026-09-09/ 301" in redirects

def test_no_standalone_stock_title():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    assert "<h1>创新医疗" not in page
