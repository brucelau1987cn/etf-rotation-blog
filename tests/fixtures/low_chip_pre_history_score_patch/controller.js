    (function() {
      var list = document.getElementById('chip-list');
      var pager = document.getElementById('chip-pager');
      var nums = document.getElementById('chip-page-nums');
      var meta = document.getElementById('chip-page-meta');
      var prevBtn = document.getElementById('chip-page-prev');
      var nextBtn = document.getElementById('chip-page-next');
      var searchInput = document.getElementById('chip-search-input');
      var searchSuggestions = document.getElementById('chip-search-suggestions');
      var empty = document.getElementById('chip-search-empty');
      var filterButtons = Array.from(document.querySelectorAll('.chip-filter-btn'));
      var filterReset = document.getElementById('chip-filter-reset');
      var filterMeta = document.getElementById('chip-filter-meta');

      var calendarBtn = document.getElementById('chip-history-calendar-btn');
      var calendarValue = document.getElementById('chip-history-calendar-value');
      var calendarPanel = document.getElementById('chip-history-calendar');
      var calendarGrid = document.getElementById('chip-calendar-grid');
      var calendarTitle = document.getElementById('chip-calendar-month-title');
      var calendarMonthPrev = document.getElementById('chip-calendar-month-prev');
      var calendarMonthNext = document.getElementById('chip-calendar-month-next');
      var historyPrev = document.getElementById('chip-history-prev');
      var historyNext = document.getElementById('chip-history-next');
      var historyStatus = document.getElementById('chip-history-status');
      var asOfEl = document.getElementById('chip-as-of');
      var countEl = document.getElementById('chip-count');
      var dataNote = document.getElementById('chip-data-note');
      if (!list || !pager || !nums || !meta || !prevBtn || !nextBtn) return;

      var PAGE_SIZE = Math.max(1, Number(pageSize || pager.dataset.pageSize || 6));
      var page = 1;
      var query = '';
      var activeFilters = new Set();

      var currentDate = String((initialData && initialData.data_as_of) || '');
      var dates = (historyIndex && historyIndex.dates) || [];
      var historyItemsByDate = Object.fromEntries(((historyIndex && historyIndex.items) || []).map(function(item) { return [item.date, item]; }));
      var calendarMonth = (currentDate || dates[0] || new Date().toISOString().slice(0, 10)).slice(0, 7);
      var cache = {};
      if (initialData && initialData.data_as_of) cache[initialData.data_as_of] = initialData;
      var loading = false;
      var suggestionIndex = -1;

      function normalize(v) { return String(v || '').trim().toLowerCase().replace(/\s+/g, ''); }
      function fmt(value, digits) {
        digits = digits == null ? 2 : digits;
        var n = Number(value);
        return Number.isFinite(n) ? n.toFixed(digits) : '—';
      }
      function fmtInt(value) {
        var n = Math.round(Number(value));
        return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '—';
      }
      function fmtMetric(value, digits, suffix) {
        if (value == null || value === '') return '—';
        var n = Number(value);
        if (!Number.isFinite(n)) return '—';
        return n.toFixed(digits == null ? 1 : digits) + (suffix || '');
      }
      function esc(s) {
        return String(s == null ? '' : s)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;');
      }
      function chipFocusClass(focus) {
        if (!focus) return '';
        if (focus === '非常集中' || focus === '较集中') return 'is-concentrating';
        if (focus === '分散') return 'is-dispersing';
        return '';
      }
      function occurrenceLabel(meta) {
        if (!meta) return '';
        if (meta.isNew) return '新入池';
        if (meta.isReentry) return '第' + meta.episodes + '次入池';
        if (meta.episodes > 1) return '第' + meta.episodes + '轮 · 连续' + meta.streak + '日';
        return '连续' + meta.streak + '日';
      }
      function occurrenceTitle(meta) {
        return meta ? '累计入池' + meta.appearances + '日 · 共' + meta.episodes + '轮 · 本轮连续' + meta.streak + '日' : '';
      }

      function buildRows(data) {
        return (Array.isArray(data.rows) ? data.rows : []).map(function(row) {
          return Object.assign({}, row, {
            hlpMetrics: row.hlp_metrics || row.hlpMetrics || null,
            touchstoneMetrics: row.touchstone_metrics || row.touchstoneMetrics || null,
            nameInitials: row.nameInitials || ''
          });
        });
      }

      // 综合上涨潜力分 (0-100) — 与 SSR 端 compositeScore 同口径
      // 筹码干净度35 + 主力吸筹25 + 股东收敛/筹码集中20 + 财务质地20
      function compositeOf(row) {
        var sm = row.shareholderMetrics || {};
        var fin = row.financials || {};
        var profits = row.profits || {};
        var pValues = [profits.week, profits.month, profits.quarter].filter(function(v) { return v != null && isFinite(Number(v)); }).map(Number);
        var profitScore;
        if (!pValues.length) profitScore = 15;
        else {
          var pa = pValues.reduce(function(a, b) { return a + b; }, 0) / pValues.length;
          var allZero = pValues.every(function(v) { return v <= 0.05; });
          profitScore = allZero ? 35 : pa <= 0.1 ? 32 : pa <= 0.5 ? 28 : pa <= 1.0 ? 20 : pa <= 1.3 ? 14 : 10;
        }
        var mf = Number(sm.main_force);
        var mfScore = isFinite(mf) ? (mf >= 35 ? 25 : mf >= 28 ? 20 : mf >= 22 ? 14 : 8) : 8;
        var shChg = Number(sm.shareholder_change_pct);
        var shScore = isFinite(shChg) ? (shChg <= -12 ? 16 : shChg <= -5 ? 14 : shChg < 0 ? 11 : shChg <= 6 ? 6 : 2) : 6;
        var focus = sm.chip_focus;
        shScore = Math.min(20, shScore + (focus === '非常集中' ? 4 : focus === '较集中' ? 2 : 0));
        var roe = Number(fin.roe);
        var roeScore = isFinite(roe) ? (roe >= 8 ? 8 : roe >= 4 ? 6 : roe >= 0 ? 4 : 0) : 2;
        var net = Number(fin.net_margin);
        var netScore = isFinite(net) ? (net >= 15 ? 6 : net >= 5 ? 4 : net >= 0 ? 2 : 0) : 2;
        var debt = Number(fin.debt_ratio);
        var debtScore = isFinite(debt) ? (debt < 30 ? 6 : debt < 50 ? 4 : debt < 70 ? 2 : 0) : 2;
        var finScore = Math.min(20, roeScore + netScore + debtScore);
        return Math.round(profitScore + mfScore + shScore + finScore);
      }

      function rowHtml(row, index, asOf) {
        var change = Number(row.change_percent);
        var changeCls = change >= 0 ? 'chip-up' : 'chip-down';
        var changeText = (change >= 0 ? '+' : '') + fmt(change) + '%';
        var badge = '';
        var hlp = row.hlpMetrics || null;
        var touchstone = row.touchstoneMetrics || null;
        if (hlp && Array.isArray(hlp.chip_signals) && hlp.chip_signals.length) {
          badge += '<span class="chip-hlp-badge" title="HLP筹码顶底：' + esc(hlp.chip_signals.join('、')) + '">筹码顶底</span>';
        }
        if (row.qualityShareholder) {
          badge += '<span class="chip-quality" title="优质股东：' + esc((row.qualityShareholderNames || []).join('、')) + '">优质股东 ✓</span>';
        } else if (row.institutionalShareholder) {
          badge += '<span class="chip-institutional" title="机构股东：' + esc((row.institutionalShareholderNames || []).join('、')) + '">机构股东 ●</span>';
        }
        var themes = Array.isArray(row.themeConcepts)
          ? row.themeConcepts.filter(Boolean).slice(0, 3)
          : (row.themeConcept ? String(row.themeConcept).split(/[、,，]/).map(function(s){return s.trim();}).filter(Boolean).slice(0, 3) : []);
        if (touchstone && (touchstone.touchstone_hit ?? touchstone.bottom_alert ?? touchstone.bottom_confirmed)) {
          var touchstoneTitle = touchstone.candidate_bottom_active
            ? '点石成金：候选底部，锚点 ' + esc(touchstone.candidate_anchor_date || '') + '，当前反弹 ' + esc(touchstone.candidate_rebound_pct ?? '') + '%'
            : '点石成金：正式确认，锚点 ' + esc(touchstone.anchor_date || '') + '，上涨 ' + esc(touchstone.rise_pct ?? '') + '%';
          badge += '<span class="chip-hlp-badge" title="' + touchstoneTitle + '">点石成金</span>';
        }
        var themeText = themes.join('、');
        var asOfLabel = String(asOf || '').replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1/$2/$3') || String(asOf || '');
        var sm = row.shareholderMetrics || {};
        var tech = row.technical || {};
        var occurrence = row.occurrence || null;
        var occurrenceHtml = occurrence ? '<span class="chip-occurrence' + (occurrence.isNew ? ' is-new' : '') + (occurrence.isReentry ? ' is-reentry' : '') + '" title="' + esc(occurrenceTitle(occurrence)) + '">' + esc(occurrenceLabel(occurrence)) + '</span>' : '';
        var changePct = sm.shareholder_change_pct;
        var changeClass = Number(changePct) <= 0 ? 'is-concentrating' : 'is-dispersing';
        var shrinkLabel = function(label) {
          var map = { '轻度控盘': '轻度', '中度控盘': '中度', '高度控盘': '完全', '不控盘': '不控', '无控盘': '不控' };
          return map[label] || label || '';
        };
        var etfHoldings = Array.isArray(row.etfHoldings) ? row.etfHoldings : [];
        var etfTopCategory = row.etfTopCategory || null;
        var profits = row.profits || {};
        var composite = row.composite != null ? row.composite : compositeOf(row);
        var reco = composite >= 70 ? '高' : composite >= 55 ? '中' : '低';
        var recoHtml = '<span class="chip-reco is-' + reco + '" title="推荐度：' + reco + '（' + composite + '/100）">' + reco + '</span>';
        return (
          '<div class="chip-row" data-symbol="' + esc(row.symbol) + '" data-name="' + esc(row.name) + '" data-initials="' + esc(row.nameInitials) + '" data-industry="' + esc(row.industry) + '" data-theme="' + esc(themeText) + '" data-roe="' + esc(row.financials.roe ?? '') + '" data-net-margin="' + esc(row.financials.net_margin ?? '') + '" data-cash-profit="' + esc(row.financials.cash_profit_ratio ?? '') + '" data-gross-margin="' + esc(row.financials.gross_margin ?? '') + '" data-debt-ratio="' + esc(row.financials.debt_ratio ?? '') + '" data-year-profit="' + esc(row.yearProfit ?? row.year_profit ?? '') + '" data-week-profit="' + esc(profits.week ?? '') + '" data-month-profit="' + esc(profits.month ?? '') + '" data-quarter-profit="' + esc(profits.quarter ?? '') + '" data-composite="' + esc(composite) + '" data-main-force="' + esc(sm.main_force ?? '') + '" data-shareholder-change="' + esc(sm.shareholder_change_pct ?? '') + '" data-change="' + esc(row.change_percent ?? '') + '" data-hlp-chip-signal="' + (hlp && Array.isArray(hlp.chip_signals) && hlp.chip_signals.length ? '1' : '0') + '" data-touchstone="' + (touchstone && (touchstone.touchstone_hit ?? touchstone.bottom_alert ?? touchstone.bottom_confirmed) ? '1' : '0') + '" data-quality-shareholder="' + (row.qualityShareholder ? '1' : '0') + '" data-institutional-shareholder="' + (row.institutionalShareholder ? '1' : '0') + '" data-board-index="' + index + '"' + (index >= PAGE_SIZE ? ' hidden' : '') + '>' +
            '<div class="chip-row-top">' +
              '<div class="chip-row-main">' +
                '<div class="chip-identity"><strong class="chip-row-name">' + esc(row.name) + '</strong>' + recoHtml + occurrenceHtml + '<span class="chip-row-code">' + esc(row.symbol) + '</span></div>' + badge +
              '</div>' +
              '<div class="chip-row-metrics" aria-label="观察日期">' +
                '<span class="chip-metric chip-metric-asof" title="查询日期股价"><em class="chip-val chip-asof-val">' + esc(asOfLabel) + '</em><small>日期</small></span>' +
              '</div>' +
              '<div class="chip-row-footer">' +
                '<span class="chip-price">¥' + fmt(row.price) + '</span>' +
                '<span class="chip-change ' + changeCls + '">' + changeText + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="chip-row-shareholders" aria-label="' + esc(row.name) + '股东与筹码指标">' +
              '<button type="button" class="chip-shr-toggle" aria-expanded="false"><span>股东与筹码指标</span><i class="chip-shr-chev" aria-hidden="true">▾</i></button>' +
              '<div class="chip-shr-head" aria-hidden="true"><span>股东人数</span><span>股东变化</span><span>筹码集中度</span><span>主力控盘</span><span>十大流通股东</span><span>报告期</span></div>' +
              '<div class="chip-shr-body">' +
                '<span><strong>' + fmtInt(sm.shareholder_count) + '</strong></span>' +
                '<span><strong class="' + changeClass + '">' + fmtMetric(changePct, 2, '%') + '</strong></span>' +
                '<span><strong class="' + chipFocusClass(sm.chip_focus) + '">' + esc(sm.chip_focus || '—') + '</strong></span>' +
                '<span><strong>' + esc(sm.main_force || '—') + (sm.main_force && sm.main_force_label ? '（' + esc(shrinkLabel(sm.main_force_label)) + '）' : '') + '</strong></span>' +
                '<span><strong>' + fmtMetric(sm.top10_float_ratio, 1, '%') + '</strong></span>' +
                '<span><strong>' + esc(sm.report_period || '—') + '</strong></span>' +
              '</div>' +
            '</div>' +
            renderIndustryEtfSection(row) +
          '</div>'
        );
      }

      function renderIndustryEtfSection(row) {
        var etf = Array.isArray(row.industryEtfs) ? row.industryEtfs : [];
        var pills = etf.map(function(h) {
          var profitHtml = (h.profit_ratio != null)
            ? '<span class="chip-etf-profit"><span class="chip-etf-profit-icon">◒</span><small>获利盘</small><strong>' + fmt(h.profit_ratio) + '%</strong></span>'
            : '';
          return '<span class="chip-etf-pill" title="' + esc((h.name || '') + ' · ' + (h.theme || '') + ' · ' + (h.match_type || '') + (h.profit_ratio != null ? ' · 收盘获利 ' + fmt(h.profit_ratio) + '%' : '')) + '">' +
                   '<span class="chip-etf-head"><span class="chip-etf-name">' + esc(h.name) + '</span>' +
                   '<span class="chip-etf-meta chip-etf-code">' + esc(h.code) + '</span></span>' +
                   profitHtml +
                 '</span>';
        }).join('');
        var emptyText = row.industryEtfStatus === 'industry_missing'
          ? '行业待补充'
          : (row.industryEtfStatus === 'unknown' ? '映射尚未计算' : '库内暂无匹配');
        var content = pills
          ? '<div class="chip-etf-list">' + pills + '</div>'
          : '<span class="chip-etf-meta chip-etf-empty-text">' + emptyText + '</span>';
        return '<div class="chip-row-etf">' + content + '</div>';
      }

      function bindShareholderToggles() {
        Array.from(list.querySelectorAll('.chip-shr-toggle')).forEach(function(btn) {
          btn.addEventListener('click', function() {
            var row = btn.closest('.chip-row');
            if (!row) return;
            var open = row.classList.toggle('is-shr-open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
          });
        });
      }

      function rowsEls() {
        return Array.from(list.querySelectorAll('.chip-row:not(.chip-row-empty)'));
      }

      function closeSuggestions() {
        if (!searchSuggestions || !searchInput) return;
        searchSuggestions.hidden = true;
        searchInput.setAttribute('aria-expanded', 'false');
        suggestionIndex = -1;
      }

      function renderSuggestions() {
        if (!searchSuggestions || !searchInput) return;
        var matches = !query ? [] : rowsEls().filter(function(c) {
          var values = [c.dataset.symbol, c.dataset.name, c.dataset.initials, c.dataset.industry].map(normalize);
          return values.some(function(value) { return value.indexOf(query) !== -1; });
        }).slice(0, 8);
        searchSuggestions.replaceChildren();
        suggestionIndex = -1;
        if (!matches.length) { closeSuggestions(); return; }
        matches.forEach(function(row, index) {
          var option = document.createElement('button');
          option.type = 'button';
          option.className = 'chip-search-option';
          option.setAttribute('role', 'option');
          option.innerHTML = '<strong>' + esc(row.dataset.name) + '</strong><span>' + esc(row.dataset.symbol) + '</span><small>' + esc(row.dataset.initials) + '</small>';
          option.addEventListener('mousedown', function(event) { event.preventDefault(); });
          option.addEventListener('click', function() {
            searchInput.value = row.dataset.symbol || row.dataset.name;
            query = normalize(searchInput.value);
            closeSuggestions();
            page = 1;
            renderPager();
            searchInput.focus();
          });
          searchSuggestions.appendChild(option);
        });
        searchSuggestions.hidden = false;
        searchInput.setAttribute('aria-expanded', 'true');
      }

      function filtered() {
        return rowsEls().filter(function(c) {
          var sym = String(c.dataset.symbol || '').toLowerCase();
          var name = String(c.dataset.name || '').toLowerCase();
          var initials = String(c.dataset.initials || '').toLowerCase();
          var industry = String(c.dataset.industry || '').toLowerCase();
          var searchMatched = !query || sym.indexOf(query) !== -1 || name.indexOf(query) !== -1 || initials.indexOf(query) !== -1 || initials.indexOf(query) === 0 || industry.indexOf(query) !== -1;
          if (!searchMatched) return false;
          if (activeFilters.has('roe') && (c.dataset.roe === '' || !Number.isFinite(Number(c.dataset.roe)) || Number(c.dataset.roe) < 15)) return false;
          if (activeFilters.has('net-margin') && (c.dataset.netMargin === '' || !Number.isFinite(Number(c.dataset.netMargin)) || Number(c.dataset.netMargin) < 15)) return false;
          if (activeFilters.has('cash-profit') && (c.dataset.cashProfit === '' || !Number.isFinite(Number(c.dataset.cashProfit)) || Number(c.dataset.cashProfit) < 20)) return false;
          if (activeFilters.has('gross-margin') && (c.dataset.grossMargin === '' || !Number.isFinite(Number(c.dataset.grossMargin)) || Number(c.dataset.grossMargin) < 15)) return false;
          if (activeFilters.has('debt-ratio') && (c.dataset.debtRatio === '' || !Number.isFinite(Number(c.dataset.debtRatio)) || Number(c.dataset.debtRatio) > 30)) return false;

          if (activeFilters.has('hlp-chip-signal') && c.dataset.hlpChipSignal !== '1') return false;
          if (activeFilters.has('touchstone') && c.dataset.touchstone !== '1') return false;
          if (activeFilters.has('quality-shareholder') && c.dataset.qualityShareholder !== '1') return false;
          if (activeFilters.has('institutional-shareholder') && c.dataset.institutionalShareholder !== '1') return false;
          return true;
        });
      }

      function renderPager() {
        var matched = filtered();
        var totalPages = Math.max(1, Math.ceil(matched.length / PAGE_SIZE));
        if (page > totalPages) page = totalPages;
        if (page < 1) page = 1;
        var start = (page - 1) * PAGE_SIZE;
        var end = start + PAGE_SIZE;
        var visible = matched.slice(start, end);
        rowsEls().forEach(function(c) { c.hidden = true; });
        visible.forEach(function(c) { c.hidden = false; });
        if (empty) empty.hidden = matched.length > 0;
        var showPager = matched.length > PAGE_SIZE;
        pager.hidden = !showPager;
        meta.textContent = matched.length ? ('第 ' + page + '/' + totalPages + ' 页 · ' + matched.length + ' 只') : '无匹配';
        if (filterMeta) filterMeta.textContent = activeFilters.size ? ('已启用 ' + activeFilters.size + ' 项 · ' + matched.length + ' 只') : '未启用筛选';

        nums.replaceChildren();
        if (showPager) {
          var pageItems = [1, 2, page - 1, page, page + 1, totalPages - 1, totalPages]
            .filter(function(value) { return value >= 1 && value <= totalPages; })
            .filter(function(value, index, values) { return values.indexOf(value) === index; })
            .sort(function(a, b) { return a - b; });
          pageItems.forEach(function(item, index) {
            if (index > 0 && item - pageItems[index - 1] > 1) {
              var dots = document.createElement('span');
              dots.className = 'chip-page-ellipsis';
              dots.textContent = '…';
              nums.appendChild(dots);
            }
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'chip-page-num' + (item === page ? ' is-active' : '');
            btn.textContent = String(item);
            btn.setAttribute('aria-label', '第 ' + item + ' 页');
            if (item === page) btn.setAttribute('aria-current', 'page');
            btn.addEventListener('click', function() { page = item; renderPager(); });
            nums.appendChild(btn);
          });
        }
        prevBtn.disabled = page <= 1 || matched.length === 0;
        nextBtn.disabled = page >= totalPages || matched.length === 0;
      }

      function setStatus(text, isError) {
        if (!historyStatus) return;
        historyStatus.textContent = text;
        historyStatus.classList.toggle('is-error', !!isError);
      }

      function monthShift(month, delta) {
        var parts = month.split('-').map(Number);
        var d = new Date(parts[0], parts[1] - 1 + delta, 1);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
      }

      function renderCalendar() {
        if (!calendarGrid || !calendarTitle) return;
        var parts = calendarMonth.split('-').map(Number);
        var year = parts[0];
        var month = parts[1];
        var first = new Date(year, month - 1, 1);
        var daysInMonth = new Date(year, month, 0).getDate();
        var leading = (first.getDay() + 6) % 7;
        calendarTitle.textContent = year + '年' + month + '月';
        calendarGrid.replaceChildren();
        for (var blank = 0; blank < leading; blank++) {
          var spacer = document.createElement('span');
          spacer.className = 'chip-calendar-day is-empty';
          calendarGrid.appendChild(spacer);
        }
        for (var day = 1; day <= daysInMonth; day++) {
          var date = calendarMonth + '-' + String(day).padStart(2, '0');
          var item = historyItemsByDate[date];
          var cell = document.createElement(item ? 'button' : 'span');
          cell.className = 'chip-calendar-day' + (item ? ' has-snapshot' + (item.intersection_count > 0 ? ' has-results' : ' is-zero') : '') + (date === currentDate ? ' is-selected' : '');
          if (item) {
            cell.type = 'button';
            cell.dataset.date = date;
            cell.setAttribute('aria-label', date + '，' + item.intersection_count + '只');
            cell.innerHTML = '<b>' + day + '</b><small>' + item.intersection_count + '只</small>';
            cell.addEventListener('click', function() {
              closeCalendar();
              loadDate(this.dataset.date);
            });
          } else {
            cell.innerHTML = '<b>' + day + '</b>';
          }
          calendarGrid.appendChild(cell);
        }
        var availableMonths = dates.map(function(d) { return d.slice(0, 7); });
        if (calendarMonthPrev) calendarMonthPrev.disabled = !availableMonths.some(function(m) { return m < calendarMonth; });
        if (calendarMonthNext) calendarMonthNext.disabled = !availableMonths.some(function(m) { return m > calendarMonth; });
      }

      function closeCalendar() {
        if (!calendarPanel || !calendarBtn) return;
        calendarPanel.hidden = true;
        calendarBtn.setAttribute('aria-expanded', 'false');
      }

      function toggleCalendar() {
        if (!calendarPanel || !calendarBtn) return;
        var willOpen = calendarPanel.hidden;
        calendarPanel.hidden = !willOpen;
        calendarBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        if (willOpen) {
          calendarMonth = currentDate.slice(0, 7);
          renderCalendar();
        }
      }

      function updateMeta(data) {
        var day = data.data_as_of || currentDate;
        var count = (data.rows || []).length;
        if (asOfEl) asOfEl.textContent = day;
        if (countEl) countEl.textContent = count + '只';
        if (dataNote) dataNote.textContent = '观察日期：' + day + ' · 范围：沪深A股';
        if (calendarValue) calendarValue.textContent = day;
        calendarMonth = day.slice(0, 7);
        var latest = dates[0] || day;
        setStatus(day === latest ? '最新快照' : ('历史 · ' + day), false);
        var idx = dates.indexOf(day);
        if (historyPrev) historyPrev.disabled = idx < 0 || idx >= dates.length - 1 || loading;
        if (historyNext) historyNext.disabled = idx <= 0 || loading;
        if (calendarBtn) calendarBtn.disabled = loading;
        if (calendarPanel && !calendarPanel.hidden) renderCalendar();
      }

      function paint(data) {
        currentDate = data.data_as_of || currentDate;
        var rows = buildRows(data);
        var html = rows.map(function(row, index) { return rowHtml(row, index, currentDate); }).join('') +
          '<div class="chip-row chip-row-empty" id="chip-search-empty" hidden><strong>没有匹配标的</strong><span>换个代码、名称或首字母试试</span></div>';
        list.innerHTML = html;
        empty = document.getElementById('chip-search-empty');
        bindShareholderToggles();
        page = 1;
        updateMeta(data);
        renderPager();
        renderSuggestions();
      }

      function loadDate(day) {
        if (!day || day === currentDate || loading) return;
        if (cache[day]) {
          paint(cache[day]);
          return;
        }
        loading = true;
        setStatus('加载中…', false);
        if (calendarBtn) calendarBtn.disabled = true;
        closeCalendar();
        if (historyPrev) historyPrev.disabled = true;
        if (historyNext) historyNext.disabled = true;
        fetch('/api/public/v1/low-chip-metrics?date=' + day)
          .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then(function(resp) {
            var data = { data_as_of: day, rows: [] };
            if (resp.results) {
              resp.results.forEach(function(row) {
                var code = row.stock_code;
                var stockName = row.stock_name || '';
                var themes = row.theme_concepts ? JSON.parse(row.theme_concepts) : [];
                data.rows.push({
                  symbol: code,
                  name: stockName || code,
                  nameInitials: stockInitialsByCode[code] || '',
                  price: row.price,
                  change_percent: row.change_percent,
                  industry: row.industry || '待补充',
                  sector: row.sector || '待补充',
                  themeConcept: themes.slice(0, 3).join('、'),
                  themeConcepts: themes.slice(0, 3),
                  financials: row.financials ? JSON.parse(row.financials) : {},
                  technical: row.technical ? JSON.parse(row.technical) : {},
                  hlpMetrics: row.hlp_metrics || null,
                  touchstoneMetrics: row.touchstone_metrics || null,
                  industryEtfs: Array.isArray(row.industry_etfs) ? row.industry_etfs : [],
                  industryEtfStatus: row.industry_etf_status || 'unknown',
                  industryEtfPoolCount: row.industry_etf_pool_count ?? null,
                  yearProfit: row.year_profit ?? null,
                  occurrence: occurrenceLookup[day]?.[code] || null,
                  qualityShareholder: Boolean(row.shareholder_nature?.quality_shareholder ?? row.quality_shareholder),
                  qualityShareholderNames: row.shareholder_nature?.quality_shareholder_names || [],
                  institutionalShareholder: Boolean(row.shareholder_nature?.institutional_shareholder),
                  institutionalShareholderNames: row.shareholder_nature?.institutional_shareholder_names || [],
                  shareholderMetrics: {
                    shareholder_count: row.shareholder_count,
                    shareholder_change_pct: row.shareholder_change_pct,
                    chip_focus: row.chip_focus,
                    main_force: row.main_force,
                    main_force_label: row.main_force_label,
                    top10_float_ratio: row.top10_float_ratio,
                    report_period: row.report_period,
                    price: row.price,
                  },
                  profits: {
                    week: row.week_profit ?? null,
                    month: row.month_profit ?? null,
                    quarter: row.quarter_profit ?? null,
                  },
                  composite: compositeOf({
                    financials: row.financials ? JSON.parse(row.financials) : {},
                    shareholderMetrics: {
                      shareholder_change_pct: row.shareholder_change_pct,
                      main_force: row.main_force,
                      chip_focus: row.chip_focus,
                    },
                    profits: {
                      week: row.week_profit ?? null,
                      month: row.month_profit ?? null,
                      quarter: row.quarter_profit ?? null,
                    },
                  })
                });
              });
            }
            cache[day] = data;
            loading = false;
            paint(data);
          })
          .catch(function(err) {
            loading = false;
            setStatus('加载失败', true);
            if (calendarBtn) calendarBtn.disabled = false;
            if (calendarValue) calendarValue.textContent = currentDate;
            updateMeta(cache[currentDate] || { data_as_of: currentDate, rows: [] });
            console.error(err);
          });
      }

      prevBtn.addEventListener('click', function() { if (page > 1) { page--; renderPager(); } });
      nextBtn.addEventListener('click', function() { page++; renderPager(); });
      if (searchInput) {
        searchInput.addEventListener('input', function() { query = normalize(searchInput.value); page = 1; renderPager(); renderSuggestions(); });
        searchInput.addEventListener('focus', renderSuggestions);
        searchInput.addEventListener('keydown', function(event) {
          var options = searchSuggestions ? Array.from(searchSuggestions.querySelectorAll('.chip-search-option')) : [];
          if (event.key === 'Escape') { closeSuggestions(); return; }
          if (!options.length || searchSuggestions.hidden) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            suggestionIndex = (suggestionIndex + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length;
            options.forEach(function(option, index) { option.classList.toggle('is-active', index === suggestionIndex); option.setAttribute('aria-selected', index === suggestionIndex ? 'true' : 'false'); });
          } else if (event.key === 'Enter' && suggestionIndex >= 0) {
            event.preventDefault();
            options[suggestionIndex].click();
          }
        });
      }
      filterButtons.forEach(function(btn) {
        btn.addEventListener('click', function() {
          var key = btn.dataset.filter;
          if (!key) return;
          if (activeFilters.has(key)) activeFilters.delete(key); else activeFilters.add(key);
          btn.classList.toggle('is-active', activeFilters.has(key));
          btn.setAttribute('aria-pressed', activeFilters.has(key) ? 'true' : 'false');
          page = 1;
          renderPager();
        });
      });
      if (filterReset) filterReset.addEventListener('click', function() {
        activeFilters.clear();
        filterButtons.forEach(function(btn) {
          btn.classList.remove('is-active');
          btn.setAttribute('aria-pressed', 'false');
        });
        page = 1;
        renderPager();
      });

      if (calendarBtn) calendarBtn.addEventListener('click', function(event) { event.stopPropagation(); toggleCalendar(); });
      if (calendarPanel) calendarPanel.addEventListener('click', function(event) { event.stopPropagation(); });
      if (calendarMonthPrev) calendarMonthPrev.addEventListener('click', function() { calendarMonth = monthShift(calendarMonth, -1); renderCalendar(); });
      if (calendarMonthNext) calendarMonthNext.addEventListener('click', function() { calendarMonth = monthShift(calendarMonth, 1); renderCalendar(); });
      document.addEventListener('click', closeCalendar);
      document.addEventListener('click', function(event) {
        if (searchSuggestions && !searchSuggestions.contains(event.target) && event.target !== searchInput) closeSuggestions();
      });
      document.addEventListener('keydown', function(event) { if (event.key === 'Escape') closeCalendar(); });
      if (historyPrev) historyPrev.addEventListener('click', function() {
        var idx = dates.indexOf(currentDate);
        if (idx >= 0 && idx < dates.length - 1) loadDate(dates[idx + 1]);
      });
      if (historyNext) historyNext.addEventListener('click', function() {
        var idx = dates.indexOf(currentDate);
        if (idx > 0) loadDate(dates[idx - 1]);
      });

      // support ?date=YYYY-MM-DD deep link
      try {
        var params = new URLSearchParams(window.location.search);
        var qDate = params.get('date');
        if (qDate && dates.indexOf(qDate) !== -1 && qDate !== currentDate) {
          loadDate(qDate);
        } else {
          bindShareholderToggles();
          updateMeta(initialData);
          renderPager();
        }
      } catch (e) {
        bindShareholderToggles();
        updateMeta(initialData);
        renderPager();
      }
    })();
    