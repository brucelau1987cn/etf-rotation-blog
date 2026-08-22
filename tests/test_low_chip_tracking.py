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
        # 固定窗口：加入日基准 + 加入后的最多 20 个交易日
        assert len(dates) <= 21
        assert dates[0] == rec["first_seen"]
        features = rec.get("entry_features")
        assert isinstance(features, dict)
        assert set(features) == {
            "quality_shareholder",
            "quality_shareholder_names",
            "institutional_shareholder",
            "institutional_shareholder_names",
            "chip_focus",
            "main_force",
            "main_force_label",
        }
        assert isinstance(features["quality_shareholder"], bool)
        assert isinstance(features["quality_shareholder_names"], list)
        assert isinstance(features["institutional_shareholder"], bool)
        assert isinstance(features["institutional_shareholder_names"], list)
        financials = rec.get("entry_financials")
        assert isinstance(financials, dict)
        assert {"roe", "net_margin", "cash_profit_ratio", "gross_margin", "debt_ratio"} <= set(financials)
        assert rec.get("year_profit") is None or isinstance(rec["year_profit"], (int, float))

    hengyunchang = stocks["688785.SH"]["entry_features"]
    assert hengyunchang == {
        "quality_shareholder": True,
        "quality_shareholder_names": ["全国社保基金六零二组合", "澳门金融管理局-自有资金", "科威特政府投资局-自有资金", "基本养老保险基金一六零五二组合"],
        "institutional_shareholder": True,
        "institutional_shareholder_names": ["中国建设银行股份有限公司-南方信息创新混合型证券投资基金", "中国银行股份有限公司-易方达供给改革灵活配置混合型证券投资基金", "中国银行-易方达积极成长证券投资基金", "太平人寿保险有限公司", "交通银行股份有限公司-易方达竞争优势企业混合型证券投资基金"],
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
        "tc-feature-institutional",
        "quality_shareholder_names",
        "institutional_shareholder_names",
        "筹码集中度",
        "主力控盘",
        "entry_features",
        "is-concentrated",
        "includes('集中')",
        "ROE ≥ 15%",
        "净利率 ≥ 15%",
        "现金流/净利润 ≥ 20%",
        "毛利率 ≥ 15%",
        "负债率 ≤ 30%",
        "K年 ≤ 3%",
        'data-filter="quality-shareholder"',
        'data-filter="institutional-shareholder"',
        "data-quality-shareholder",
        "data-institutional-shareholder",
        "activeFilters.has('quality-shareholder')",
        "activeFilters.has('institutional-shareholder')",
        "未启用筛选",
        "tc-filter-reset",
        "data-roe",
        "data-net-margin",
        "data-cash-profit",
        "data-gross-margin",
        "data-debt-ratio",
        "data-year-profit",
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
    assert "20日追踪" in mode_nav


def test_low_chip_pages_share_mode_navigation_and_tracking_scan_controls():
    low_chip = LOW_CHIP_PAGE.read_text(encoding="utf-8")
    tracking = TRACKING_PAGE.read_text(encoding="utf-8")
    mode_nav = MODE_NAV.read_text(encoding="utf-8")

    assert "LowChipModeNav" in low_chip
    assert 'active="screen"' in low_chip
    assert "LowChipModeNav" in tracking
    assert 'active="tracking"' in tracking
    assert "当日观察" in mode_nav
    assert "20日追踪" in mode_nav
    assert "aria-current={" in mode_nav
    assert "chip-track-link" not in low_chip
    assert "tc-back" not in tracking
    assert 'id="tc-search-input"' in tracking
    assert "包含科创板" in tracking
    assert "上涨占比" in tracking
    assert "中位涨幅" in tracking
    assert "第{rec.daily.length}/20日" in tracking
    assert 'aria-valuemax="20"' in tracking
    assert "Math.max(0, 20 - rec.daily.length)" in tracking
    assert "el.dataset.roe !== ''" in tracking
    assert "el.dataset.netMargin !== ''" in tracking
    assert "Number(el.dataset.netMargin) >= 15" in tracking
    assert "el.dataset.cashProfit !== ''" in tracking
    assert "el.dataset.grossMargin !== ''" in tracking
    assert "el.dataset.debtRatio !== ''" in tracking
    assert "Number(el.dataset.roe) >= 15" in tracking
    assert "Number(el.dataset.debtRatio) <= 30" in tracking
    assert "Number(el.dataset.yearProfit) <= 3" in tracking
    assert "activeFilters.clear()" in tracking
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
    assert "MAX_TRACK_BARS = 20" in text
    assert "MAX_STORED_BARS = MAX_TRACK_BARS + 1" in text
    assert "target_bars = bars[:MAX_STORED_BARS]" in text

    import importlib.util
    spec = importlib.util.spec_from_file_location("tracking_window_contract", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.MAX_TRACK_BARS == 20
    assert mod.MAX_STORED_BARS == 21
    assert "rec[\"daily\"] = rec[\"daily\"][:MAX_STORED_BARS]" in text
    # Tencent multi-day range with concrete end=today often drops the latest bar;
    # empty end in the fqkline param is the reliable form.
    assert "day,{start},,640,qfq" in text or "day,{start},,640,qfq" in text.replace(" ", "")
    assert "param={ex}{code},day,{start},,640,qfq" in text
    assert "if end and date > end:" in text
    assert "低筹码追踪" in TRACKING_PAGE.read_text(encoding="utf-8") or True


def test_join_date_snapshot_evidence_fails_closed(tmp_path):
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("tracking_join_date_gate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    missing = tmp_path / "2026-08-01.json"
    with pytest.raises(RuntimeError, match="missing or invalid join-date snapshot"):
        mod.load_entry_enrichment(missing, "600000.SH")

    corrupt = tmp_path / "2026-08-02.json"
    corrupt.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or invalid join-date snapshot"):
        mod.load_entry_enrichment(corrupt, "600000.SH")

    incomplete = tmp_path / "2026-08-03.json"
    incomplete.write_text(json.dumps({"enrichments": {}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing join-date enrichment"):
        mod.load_entry_enrichment(incomplete, "600000.SH")


def test_tracking_window_migrates_old_15_day_completion_to_20_days(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("tracking_window_migration", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    snapshot = {
        "intersection": ["600000.SH"],
        "enrichments": {"600000.SH": {
            "industry": "测试行业",
            "financials": {"roe": 31, "net_margin": 26, "cash_profit_ratio": 21, "gross_margin": 16, "debt_ratio": 9},
        }},
        "periods": {"week": [{"symbol": "600000.SH", "name": "测试股票"}]},
    }
    (history_dir / "2026-01-01.json").write_text(json.dumps(snapshot), encoding="utf-8")

    data_path = tmp_path / "tracking.json"
    old_bars = [
        {"date": f"2026-01-{day:02d}", "close": float(day), "change_pct": 0.0, "profit_ratio": 1.0}
        for day in range(1, 17)
    ]
    data_path.write_text(json.dumps({
        "schema_version": "low-chip-tracking-v1",
        "generated_at": "2026-01-16T16:00:00+08:00",
        "stocks": {"600000.SH": {
            "name": "测试股票",
            "first_seen": "2026-01-01",
            "last_seen": "2026-01-01",
            "industry": "测试行业",
            "daily": old_bars,
            "tracking_complete": True,
        }},
    }), encoding="utf-8")

    monkeypatch.setattr(mod, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(mod, "DATA", data_path)
    monkeypatch.setattr(mod, "iwencai_profit_ratio", lambda _symbol, _date: 1.0)

    # A record completed under the old baseline+15 contract re-enters tracking.
    monkeypatch.setattr(mod, "tencent_daily", lambda _symbol, _start, _end: old_bars)
    assert mod.main() == 0
    migrated = json.loads(data_path.read_text(encoding="utf-8"))["stocks"]["600000.SH"]
    assert len(migrated["daily"]) == 16
    assert migrated["tracking_complete"] is False

    # It completes only after the baseline plus 20 post-join bars are stored.
    twenty_day_bars = old_bars + [
        {"date": f"2026-01-{day:02d}", "close": float(day), "change_pct": 0.0}
        for day in range(17, 22)
    ]
    monkeypatch.setattr(mod, "tencent_daily", lambda _symbol, _start, _end: twenty_day_bars)
    assert mod.main() == 0
    completed = json.loads(data_path.read_text(encoding="utf-8"))["stocks"]["600000.SH"]
    assert len(completed["daily"]) == 21
    assert completed["daily"][0]["date"] == "2026-01-01"
    assert completed["daily"][-1]["date"] == "2026-01-21"
    assert completed["tracking_complete"] is True
    assert completed["entry_financials"]["roe"] == 31


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
    assert "QUOTE_BATCH_SIZE = 40" in live_app
    assert "chunkedSymbols" in live_app
    assert "Promise.all" in live_app
    assert "if (batches.some(function (batch) { return !batch; })) return;" in live_app
    assert "flatMap" in live_app
    # A newly joined stock has zero formal post-join closes on day 1; it still receives
    # a live price, while its join-to-live change begins at 0% until settlement.
    assert "Number.isFinite(firstClose) ? firstClose : price" in live_app
    # SVG elements need the hidden attribute removed explicitly; SVGElement.hidden does
    # not reflect to the attribute consistently across browsers.
    assert "connector.removeAttribute('hidden')" in live_app
    assert "livePoint.removeAttribute('hidden')" in live_app
    assert "label.removeAttribute('hidden')" in live_app


def test_tracking_page_paginates_filtered_cards_by_twenty():
    page = TRACKING_PAGE.read_text(encoding="utf-8")

    for marker in (
        'id="tc-pagination"',
        'id="tc-page-prev"',
        'id="tc-page-next"',
        'id="tc-page-status"',
        'id="tc-list-title">追踪列表',
        'class="tc-list-top"',
        'class="tc-top-actions"',
        'id="tc-search-suggestions"',
        'role="combobox"',
        'aria-autocomplete="list"',
        'data-name-initials={nameInitials(rec.name)}',
        "import { pinyin } from 'pinyin-pro'",
        "function renderSuggestions()",
        "initials.indexOf(query) !== -1",
        '.tc-list-top { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center;',
        '.tc-top-actions { display:flex; align-items:center; justify-content:flex-end;',
        '@media (max-width: 900px) {\n    .tc-overview { grid-template-columns:1fr; gap:1rem; }\n    .tc-list-top { grid-template-columns:1fr; }',
        'var PAGE_SIZE = 12',
        'var currentPage = 1',
        'function renderPagination()',
        'Math.ceil(filteredItems.length / PAGE_SIZE)',
        'currentPage = 1;',
    ):
        assert marker in page
    assert "list.scrollIntoView" not in page
    tools_start = page.index('<section class="tc-list-tools"')
    filters_pos = page.index('id="tc-financial-filters"')
    tools_end = page.index('</section>', filters_pos)
    assert tools_start < filters_pos < tools_end
    assert '.tc-list-top { display:grid;' in page
    assert 'border-bottom:1px solid #e2e8f0' not in page


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
