from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_jin10_calendar_page_and_navigation_contract():
    page = (ROOT / "src/pages/calendar.astro").read_text(encoding="utf-8")
    app = (ROOT / "public/js/jin10-calendar-app.js").read_text(encoding="utf-8")
    footer = (ROOT / "src/components/Footer.astro").read_text(encoding="utf-8")
    futures = (ROOT / "src/pages/futures-compass/index.astro").read_text(encoding="utf-8")

    assert "财经日历" in page
    assert 'id="calendar-date"' in page
    assert 'id="calendar-list"' in page
    assert 'data-calendar-filter="important"' in page
    assert 'data-calendar-filter="important-data"' in page
    assert 'data-calendar-filter="important-event"' in page
    assert "/api/public/v1/jin10-calendar" in app
    assert "/api/public/v1/jin10-mcp-calendar" in app
    assert "mergeAffectTxt" in app
    assert "impactText" in app
    assert "'bearish'" in app
    assert "'bullish'" in app
    assert "'neutral'" in app
    assert "impact-" in app
    assert "activeFilter = 'important'" in app
    assert 'href="/futures-compass/jin10/"' in futures
    assert '宏观数据' in (ROOT / "src/components/FuturesSubnav.astro").read_text(encoding="utf-8")
    assert '宏观数据' in (ROOT / "src/pages/futures-compass/jin10.astro").read_text(encoding="utf-8")
    assert "Veilx CDN" in footer
    assert "veilx.io/#/hello/3B2WSRN2" in footer
    assert "footer-links" not in footer
    assert "promo-cdn-copy ul" in footer
    assert ".impact-tag.impact-bullish" in page
    assert ".impact-tag.impact-neutral" in page


def test_jin10_skill_is_committed_without_credentials():
    skill = (ROOT / "skills/research/jin10-calendar/SKILL.md").read_text(encoding="utf-8")
    assert "week_info" in skill
    assert "getDataById" in skill
    assert "getDataListByIndId" in skill
    assert "getDataByIndIdAndDateRange" in skill
    assert "x-token=" not in skill
    assert "Cookie:" not in skill
