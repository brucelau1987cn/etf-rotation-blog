from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "pages" / "rolling.astro"
MATRIX = ROOT / "src" / "components" / "ARollingEnergyMatrix.astro"
ALERTS = ROOT / "src" / "components" / "ARollingAiAlerts.astro"
APP = ROOT / "public" / "js" / "a-rolling-app.js"
STATS = ROOT / "src" / "components" / "ARollingStatsStrip.astro"
STYLES = ROOT / "src" / "styles" / "a-rolling.css"
HK_PAGE = ROOT / "src" / "pages" / "rolling" / "hk.astro"
US_PAGE = ROOT / "src" / "pages" / "rolling" / "us.astro"
FUTURES_PAGE = ROOT / "src" / "pages" / "rolling" / "futures.astro"
HEADER = ROOT / "src" / "components" / "Header.astro"
FOOTER = ROOT / "src" / "components" / "Footer.astro"
SUBNAV = ROOT / "src" / "components" / "RollingSubnav.astro"


def test_primary_navigation_uses_rolling_compass_name():
    header = HEADER.read_text(encoding="utf-8")
    footer = FOOTER.read_text(encoding="utf-8")
    subnav = SUBNAV.read_text(encoding="utf-8")
    for source in (header, footer, subnav):
        assert "滚动罗盘" in source
        assert "滚动轮盘" not in source


def test_rolling_subnav_order_is_a_futures_hk_us():
    subnav = SUBNAV.read_text(encoding="utf-8")
    header = HEADER.read_text(encoding="utf-8")
    assert subnav.index("A股滚动") < subnav.index("期货滚动") < subnav.index("港股滚动") < subnav.index("美股滚动")
    assert "/rolling/futures/" in subnav
    assert "/rolling/futures" in header


def test_energy_page_renders_multi_market_rolling_shell_and_resilient_polling():
    source = PAGE.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    alerts = ALERTS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    assert "多标的双向能量传导" in matrix
    assert "AI 卖出预警实时研判" in alerts
    assert "RollingSubnav" in source
    assert 'data-market="a"' in source
    assert "/api/public/v1/rolling-signals" in app
    assert "startMarketPoll" in app
    assert "calendarMarket" in app
    assert "initialQuoteLoad" in app
    assert "initBoardPager" in app
    assert 'id="board-search-input"' in matrix
    assert 'id="board-pager"' in matrix
    assert 'data-page-size="3"' in matrix
    assert "data-initials" in matrix
    assert "上下滑动" not in matrix
    assert "max-height: none" in styles
    assert ".a-rolling-main .board-pager" in styles
    assert ".a-rolling-main .board-search-input" in styles


def test_a_rolling_summary_uses_tall_signal_tickers_and_polished_indices():
    stats = STATS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'market-index-card' in stats
    assert 'class="watch-card"' not in stats
    assert 'id="stat-buy-total"' not in stats
    assert 'id="stat-sell-total"' not in stats
    assert 'id="buy-signal-track"' in stats
    assert 'id="sell-signal-track"' in stats
    assert 'summary-signal-point' in app
    assert 'summary-signal-time' in app
    assert 'id="index-refresh-countdown"' in stats
    assert 'id="buy-today-count"' in stats
    assert 'id="sell-today-count"' in stats
    assert '个信号（含观察）' in stats
    assert '最新多方信号' not in stats
    assert '最新空方信号' not in stats
    assert 'summary-signal-label' not in stats
    assert '最新 4 条' not in stats
    assert '每股最新 1 条' not in stats
    assert stats.count('signal-chip') == 3
    assert ".slice(0, 4)" not in stats
    assert ".slice(0, 4)" not in app

    index_order = [stats.index(name) for name in ("上证指数", "深证成指", "创业板指")]
    assert index_order == sorted(index_order)
    for symbol in ("000001.SH", "399001.SZ", "399006.SZ"):
        assert symbol in app

    assert "renderSummarySignals" in app
    assert "startSummaryTicker" in app
    assert "renderTodayCount" in app
    assert "todaySignalsFor" in app
    assert "isTodayShanghai" in app
    assert "SUMMARY_VISIBLE_ROWS" in app
    assert "summary-signal-count" in app
    assert "summary-signal-symbol" in app
    assert "summary-signal-identity" in app
    assert "summary-signal-tape" in app
    assert "当日累计" in app
    assert "个信号（含观察）" in app
    assert "index-refresh-countdown" in app
    assert "QUOTE_INTERVAL_MS" in app
    assert "fetchTriggerPrice" in app
    assert "summary-signal-price" in app
    assert "/api/public/v1/kline" in app
    assert "data-ticker-clone" in app
    assert "formatTriggerPrice" in app
    assert "信号点股价未入库" in app
    assert "price_source" in app
    assert "hf_XAU" in app
    assert "hf_XAG" in app
    assert "hf_CL" in app
    assert "DINIW" in app
    assert "现货黄金" in stats
    assert "现货白银" in stats
    assert "国际原油" in stats
    assert "美元指数" in stats
    assert "🟡 关注指数" in stats
    assert "三大指数" not in stats
    assert "market-24h-tag" in stats
    assert 'data-quote-mode="24h"' in stats
    assert "fetchContinuousQuotes" in app
    assert "continuousOnly" in app
    assert "sessionOnly" in app
    assert "min-height: 292px" in styles
    assert "height: 220px" in styles
    assert "SUMMARY_ROW_HEIGHT = 44" in app
    assert ".a-rolling-main .summary-signal-viewport" in styles
    assert ".a-rolling-main .summary-signal-count" in styles
    assert ".a-rolling-main .summary-signal-symbol" in styles
    assert ".a-rolling-main .summary-signal-identity" in styles
    assert ".a-rolling-main .summary-signal-tape" in styles
    assert ".a-rolling-main .summary-signal-price" in styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in styles
    assert "@media (max-width: 980px)" in styles
    assert "@media (max-width: 760px)" in styles
    assert ".a-rolling-main .summary-signal-label-text" in styles
    assert ".a-rolling-main .today-count-chip" in styles
    assert ".a-rolling-main .market-index-row" in styles
    assert ".a-rolling-main .market-spot-list" in styles
    assert ".a-rolling-main .market-24h-tag" in styles
    assert "当日更新" not in stats
    assert 'id="buy-today-track"' not in stats
    assert 'id="sell-today-track"' not in stats


def test_summary_shows_today_only_signals_and_240m_stop_validation_card():
    stats = STATS.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert "isTodayShanghai" in app
    assert "todaySignalsFor" in app
    assert "renderTodaySignals" not in app
    assert "当日更新" not in stats
    assert "stop-validation-card" in app
    assert "停止验证" in app
    assert "停止验证 240m" in app
    assert "is-stop-validation" in app
    assert "stop-validation-card" in matrix
    assert "停止验证" in matrix
    assert "item.code === '240m'" in matrix
    assert "badge-price" in matrix
    assert "formatBadgePrice" in matrix
    assert "badge-price" in app
    assert "formatBadgePrice" in app
    assert ".a-rolling-main .today-signal-row" not in styles
    assert ".a-rolling-main .stop-validation-card" in styles
    assert ".a-rolling-main .summary-signal-point.is-stop-validation" in styles
    assert ".a-rolling-main .badge-price" in styles
    assert "indexPill.textContent = text" in app


def test_hk_and_us_rolling_use_market_specific_index_cards():
    stats = STATS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    hk = HK_PAGE.read_text(encoding="utf-8")
    us = US_PAGE.read_text(encoding="utf-8")

    assert "indexMarket?: 'a' | 'futures' | 'hk' | 'us'" in stats
    assert 'indexMarket="hk"' in hk
    assert 'indexMarket="us"' in us
    for name in ("恒生指数", "恒生综合指数", "恒生科技指数"):
        assert name in stats
    for name in ("标普500指数", "纳斯达克综合指数", "道琼斯工业平均指数"):
        assert name in stats
    for symbol in ("HSI.HK", "HSCI.HK", "HSTECH.HK", "INX.US", "IXIC.US", "DJI.US"):
        assert symbol in app


def test_futures_rolling_page_sits_between_a_and_hk():
    futures = FUTURES_PAGE.read_text(encoding="utf-8")
    stats = STATS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert "期货滚动" in futures
    assert 'active="futures"' in futures
    assert 'data-market="futures"' in futures
    assert 'indexMarket="futures"' in futures
    assert "等待点名" in futures
    assert "FUTURES_INSTRUMENTS" in app
    assert "nf_AU0" in app
    assert "nf_SC0" in app
    assert "nf_M0" in app
    assert "黄金连续" in stats
    assert "原油连续" in stats
    assert "豆粕连续" in stats
    assert "empty-board-card" in matrix
    assert "暂无滚动标的" in matrix
    assert ".a-rolling-main .empty-board-card" in styles
    # Free-running poll for futures (no stock session gate).
    assert "market === 'futures' ? null" in app
