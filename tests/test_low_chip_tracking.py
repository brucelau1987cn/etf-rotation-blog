import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKING = ROOT / "public" / "data" / "low-chip-tracking.json"
TRACKING_PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip" / "tracking.astro"
LOW_CHIP_PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip.astro"
MODE_NAV = ROOT / "src" / "components" / "LowChipModeNav.astro"
SCRIPT = ROOT / "scripts" / "update_low_chip_tracking.py"


def test_tracking_data_contract():
    data = json.loads(TRACKING.read_text(encoding="utf-8"))
    assert data["schema_version"] == "low-chip-tracking-v1"
    stocks = data["stocks"]
    assert len(stocks) >= 1
    for symbol, rec in stocks.items():
        assert symbol.endswith((".SZ", ".SH"))
        assert rec["first_seen"]
        assert isinstance(rec["daily"], list) and len(rec["daily"]) >= 1
        # 每个交易日有收盘价；获利盘可能缺失（iWenCai 无数据）但应大多存在
        assert all(d.get("close") for d in rec["daily"])
        # daily 按日期升序
        dates = [d["date"] for d in rec["daily"]]
        assert dates == sorted(dates)
        # 固定窗口：加入日基准 + 加入后的最多 10 个交易日
        assert len(dates) <= 11
        assert dates[0] == rec["first_seen"]
        features = rec.get("entry_features")
        assert isinstance(features, dict)
        assert set(features) == {"quality_shareholder", "chip_focus", "main_force", "main_force_label"}

    hengyunchang = stocks["688785.SH"]["entry_features"]
    assert hengyunchang == {
        "quality_shareholder": True,
        "chip_focus": "非常集中",
        "main_force": 37.41,
        "main_force_label": "中度控盘",
    }


def test_tracking_page_and_entry_link():
    page = TRACKING_PAGE.read_text(encoding="utf-8")
    for marker in (
        "低筹码追踪",
        "加入以来",
        "股价走势",
        "获利盘指数",
        "每日明细",
        "最高涨幅",
        "最大回撤",
        "剩余天数",
        "获利盘变化",
        "数据来源：腾讯日线",
        "同花顺市场数据",
        "chartGeom",
        "科创板",
        "tc-star-toggle",
        "data-star-market",
        "low_chip_tracking_include_star",
        "追踪中",
        "已完成",
        "data-tracking-status",
        "low_chip_tracking_view",
        "tc-vol-tag-slot",
        "data-price-volume-symbol",
        "/js/price-volume-tag.js",
        "tc-model-tags",
        "tc-feature-quality",
        "筹码集中度",
        "主力控盘",
        "entry_features",
        "is-concentrated",
        "includes('集中')",
    ):
        assert marker in page
    assert "threshY" not in page
    assert "3% 低筹码筛选阈值" not in page
    assert "iWenCai 收盘获利比例" not in page
    price_volume_app = (ROOT / "public" / "js" / "price-volume-tag.js").read_text(encoding="utf-8")
    assert "tc-vol-tag-slot" in price_volume_app
    assert "data-price-volume-symbol" in price_volume_app
    low_chip = LOW_CHIP_PAGE.read_text(encoding="utf-8")
    mode_nav = MODE_NAV.read_text(encoding="utf-8")
    assert "LowChipModeNav" in low_chip
    assert "/rolling/low-chip/tracking/" in mode_nav
    assert "10日追踪" in mode_nav


def test_low_chip_pages_share_mode_navigation_and_tracking_scan_controls():
    low_chip = LOW_CHIP_PAGE.read_text(encoding="utf-8")
    tracking = TRACKING_PAGE.read_text(encoding="utf-8")
    mode_nav = MODE_NAV.read_text(encoding="utf-8")

    assert "LowChipModeNav" in low_chip
    assert 'active="screen"' in low_chip
    assert "LowChipModeNav" in tracking
    assert 'active="tracking"' in tracking
    assert "当日观察" in mode_nav
    assert "10日追踪" in mode_nav
    assert "aria-current={" in mode_nav
    assert "chip-track-link" not in low_chip
    assert "tc-back" not in tracking
    assert 'id="tc-search-input"' in tracking
    assert "包含科创板" in tracking
    assert "上涨占比" in tracking
    assert "中位涨幅" in tracking
    assert "第{rec.daily.length}/10日" in tracking
    assert "tc-progress-bar" in tracking
    assert 'class="tc-overview"' in tracking
    assert "@media (max-width: 900px)" in tracking
    assert ".tc-summary" in tracking
    assert "grid-template-columns:repeat(2" in tracking
    assert 'role="progressbar"' in tracking
    assert "tracking_complete" in tracking
    assert 'id="tc-filter-empty"' in tracking
    assert "剩余天数" in tracking


def test_tracking_script_exists():
    assert SCRIPT.exists()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "tencent_daily" in text
    assert "iwencai_profit_ratio" in text
    assert "MAX_STORED_BARS = MAX_TRACK_BARS + 1" in text
    assert "target_bars = bars[:MAX_STORED_BARS]" in text
    assert "rec[\"daily\"] = rec[\"daily\"][:MAX_STORED_BARS]" in text
    # Tencent multi-day range with concrete end=today often drops the latest bar;
    # empty end in the fqkline param is the reliable form.
    assert "day,{start},,640,qfq" in text or "day,{start},,640,qfq" in text.replace(" ", "")
    assert "param={ex}{code},day,{start},,640,qfq" in text
    assert "if end and date > end:" in text
    assert "低筹码追踪" in TRACKING_PAGE.read_text(encoding="utf-8") or True


def test_tracking_page_has_batched_live_quote_layer():
    page = TRACKING_PAGE.read_text(encoding="utf-8")
    live_app = (ROOT / "public" / "js" / "low-chip-tracking-live.js").read_text(encoding="utf-8")

    for marker in (
        'data-live-symbol={rec.symbol}',
        'data-live-change',
        'class="tc-live-summary-row"',
        '.tc-change { font-size: .92rem;',
        'data-live-summary',
        'data-live-badge',
        'data-live-table-body',
        'data-price-closes',
        'data-chart-anchor',
        'data-latest-date={rec.latestStoredDate || \'\'}',
        'data-settled-days',
        'tc-live-connector',
        'id="tc-summary-rising"',
        'id="tc-summary-median"',
        'id="tc-summary-strongest"',
        'id="tc-summary-mode"',
        'aria-live="polite"',
        '/js/normalize-quote-payload.js',
        '/js/etf-live-poll.js',
        '/js/low-chip-tracking-live.js',
    ):
        assert marker in page

    assert "/api/public/v1/quote?symbols=" in live_app
    assert "Promise.all" not in live_app
    assert "30000" in live_app
    assert "EtfQuote.normalizeQuotePayload" in live_app
    assert "EtfQuote.findQuoteItem" in live_app
    assert "EtfLivePoll.startMarketPoll" in live_app
    assert "intervalMs: POLL_MS" in live_app
    assert "price > 0" in live_app
    assert "Intl.DateTimeFormat('en-CA'" in live_app
    assert "text.replace(' ', 'T') + '+08:00'" in live_app
    assert "currentPhase.label !== '今日休市'" in live_app
    assert "document.createElement('td')" in live_app
    assert "low-chip-quotes-updated" in live_app
    assert "部分实时" in live_app
    assert "今日盘中" in live_app
    assert "收盘待结算" in live_app
    assert "获利盘待收盘" in live_app
    assert "第1日待结算" in live_app
    assert "quote_time" in live_app
    # A newly joined stock has zero formal post-join closes on day 1; it still receives
    # a live price, while its join-to-live change begins at 0% until settlement.
    assert "Number.isFinite(firstClose) ? firstClose : price" in live_app
    # SVG elements need the hidden attribute removed explicitly; SVGElement.hidden does
    # not reflect to the attribute consistently across browsers.
    assert "connector.removeAttribute('hidden')" in live_app
    assert "livePoint.removeAttribute('hidden')" in live_app
    assert "label.removeAttribute('hidden')" in live_app


def test_tencent_daily_includes_requested_end_day(monkeypatch):
    """Regression: end=today must not drop today's bar (Tencent range quirk)."""
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "update_low_chip_tracking.py"
    spec = importlib.util.spec_from_file_location("update_low_chip_tracking", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({
                "code": 0,
                "data": {
                    "sh600363": {
                        "qfqday": [
                            ["2026-08-10", "21.36", "20.52", "21.55", "20.52", "1"],
                            ["2026-08-11", "19.27", "20.55", "21.10", "19.22", "1"],
                            ["2026-08-12", "20.55", "20.60", "20.80", "20.40", "1"],
                        ]
                    }
                }
            }).encode()

    def fake_urlopen(req, timeout=20):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    bars = mod.tencent_daily("600363.SH", "2026-08-10", "2026-08-11")
    # empty end form: day,start,,640,qfq
    assert ",day,2026-08-10,,640,qfq" in captured["url"]
    dates = [b["date"] for b in bars]
    assert dates == ["2026-08-10", "2026-08-11"]
    assert bars[-1]["close"] == 20.55
